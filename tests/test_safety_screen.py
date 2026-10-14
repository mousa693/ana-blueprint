"""Deterministic safety screen: a labelled, categorised table, precision/recall,
and API shape.

Labels follow docs/ONBOARDING_AND_SAFETY.md: crisis = intent/plan/means/passive
death wish/recent attempt/current self-harm, intent to harm a real person, abuse
or danger now; concern = distress without intent, past self-harm/abuse, worry
about someone else, any (even negated) mention of being suicidal; none =
everything else, including idioms and story/game actions.

How the table was built (honest provenance). Rows were written in rounds. Each
"rN" round was written fresh and measured on the rules as they stood BEFORE any
tuning on it, and only then used to tune. Held-out results at the time:

    round  rows  crisis P / R      none->crisis  crisis->none
    r1      68   accuracy 39/68 (57%) overall; 0 false positives
    r2      71   0.833 / 0.286     1             (concept layer added after this)
    r3      73   0.900 / 0.581     2
    r4      82   0.875 / 0.778     4             8
    r5      75   0.821 / 0.676     3             11

So on genuinely unseen phrasings expect roughly 0.65-0.8 crisis recall from the
deterministic screen alone. The 100% below is IN-SAMPLE (every row has been
tuned on) and is a regression floor, not an accuracy estimate.
"""
from __future__ import annotations

import pytest

from ana.safety import (
    DISCLAIMER,
    RESOURCES,
    SAFETY_SCHEMA,
    SafetyResult,
    combine,
    normalise,
    resources_for,
    screen_text,
    screen_text_llm,
)

# (category, source, text, label). source: "orig" = first table, "dev" = first
# coverage set, "r1".."r5" = held-out rounds (each measured BEFORE tuning on it,
# then folded in). See the module docstring for the held-out history.
TABLE: list[tuple[str, str, str, str]] = [
    # ── suicide_self_harm_explicit ──
    ('suicide_self_harm_explicit', 'orig', 'I want to kill myself', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "i'm going to end my life tonight", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I've been thinking about suicide a lot", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'honestly I just want to die', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I wish I was dead', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I don't want to live anymore", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'i dont want to be alive', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'Everyone would be better off without me', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "There's no reason to live", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I'm suicidal", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I feel kind of suicidal today', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I have a plan to take my own life', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I want to go to sleep and never wake up', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'how many pills would it take to die', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I'm going to take all my pills tonight", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I keep thinking about jumping off the bridge', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I wrote a suicide note', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I want to hurt myself', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I've been cutting myself again", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I'm going to hang myself", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'k i l l m y s e l f', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I want to k1ll mys3lf', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I want to kill\u200b myself', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I WANT TO DIIIIE', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'i want to unalive myself', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I'm tired of living", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', "I don't think I want to be alive", 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'i just want it all to end', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'kms', 'crisis'),
    ('suicide_self_harm_explicit', 'orig', 'I want to end it all', 'crisis'),
    # ── passive_ideation ──
    ('passive_ideation', 'dev', 'I dont see the point in living anymore', 'crisis'),
    ('passive_ideation', 'dev', "I wish I wasn't here anymore", 'crisis'),
    ('passive_ideation', 'dev', 'I wish I had never been born', 'crisis'),
    ('passive_ideation', 'dev', 'I want to disappear forever', 'crisis'),
    ('passive_ideation', 'dev', "I can't keep going like this, I want it to stop for good", 'crisis'),
    ('passive_ideation', 'dev', 'my family would be better off without me', 'crisis'),
    ('passive_ideation', 'dev', "I'm just a burden and they'd be happier if I was gone", 'crisis'),
    ('passive_ideation', 'dev', 'sometimes I wish I could just not exist', 'crisis'),
    ('passive_ideation', 'dev', "I don't want to be here anymore", 'crisis'),
    ('passive_ideation', 'dev', 'life has no meaning for me anymore and I want out', 'crisis'),
    ('passive_ideation', 'dev', "I hope I don't wake up tomorrow", 'crisis'),
    ('passive_ideation', 'dev', "I'd be better off dead", 'crisis'),
    ('passive_ideation', 'dev', "what's the point of living", 'crisis'),
    ('passive_ideation', 'dev', "there's no point in me being alive", 'crisis'),
    ('passive_ideation', 'r1', "honestly I'd rather not be alive", 'crisis'),
    ('passive_ideation', 'r1', 'some days I wish I could go to sleep and just not wake up', 'crisis'),
    ('passive_ideation', 'r1', "I keep thinking everyone would be fine if I just wasn't around", 'crisis'),
    ('passive_ideation', 'r1', "i don't really want to exist anymore", 'crisis'),
    ('passive_ideation', 'r1', "Living feels pointless and I'd rather be dead", 'crisis'),
    ('passive_ideation', 'r1', 'i see no reason to keep living', 'crisis'),
    ('passive_ideation', 'r1', "I'm done with life", 'crisis'),
    ('passive_ideation', 'r1', 'I just want to stop existing', 'crisis'),
    ('passive_ideation', 'r1', 'nobody would even notice if I died', 'crisis'),
    ('passive_ideation', 'r1', 'my kids would be better off with me gone', 'crisis'),
    ('passive_ideation', 'r2', 'I keep wishing I could just fall asleep forever', 'crisis'),
    ('passive_ideation', 'r2', "There's nothing keeping me here anymore", 'crisis'),
    ('passive_ideation', 'r2', "I don't care if I live or die", 'crisis'),
    ('passive_ideation', 'r2', 'it would be easier for everyone if I was dead', 'crisis'),
    ('passive_ideation', 'r2', 'I feel like dying', 'crisis'),
    ('passive_ideation', 'r2', "I don't want to wake up in the morning anymore", 'crisis'),
    ('passive_ideation', 'r2', 'death sounds like a relief right now', 'crisis'),
    ('passive_ideation', 'r2', 'I wish a car would just hit me', 'crisis'),
    ('passive_ideation', 'r2', "I'm ready to go, I've had enough of this life", 'crisis'),
    ('passive_ideation', 'r2', "what's the use of living like this", 'crisis'),
    ('passive_ideation', 'r3', 'part of me wants to die', 'crisis'),
    ('passive_ideation', 'r3', 'I think about dying all the time', 'crisis'),
    ('passive_ideation', 'r3', "I'd be happy if I didn't wake up tomorrow", 'crisis'),
    ('passive_ideation', 'r3', "I'm tired of being alive", 'crisis'),
    ('passive_ideation', 'r3', "I don't think I can keep living like this much longer", 'crisis'),
    ('passive_ideation', 'r3', 'Everyone would move on fast if I was gone', 'crisis'),
    ('passive_ideation', 'r3', 'sometimes I think the world would be better without me in it', 'crisis'),
    ('passive_ideation', 'r3', "I'm just waiting to die at this point", 'crisis'),
    ('passive_ideation', 'r4', 'honestly id be ok if i just never woke up', 'crisis'),
    ('passive_ideation', 'r4', 'i want to not be alive anymore', 'crisis'),
    ('passive_ideation', 'r4', "every night I pray I won't wake up", 'crisis'),
    ('passive_ideation', 'r4', "I'm so done, I just want to die tbh", 'crisis'),
    ('passive_ideation', 'r4', 'i feel like everyone would be relieved if i died', 'crisis'),
    ('passive_ideation', 'r4', 'I keep thinking it would be easier to just not be here', 'crisis'),
    ('passive_ideation', 'r4', 'Life is not worth it anymore for me', 'crisis'),
    ('passive_ideation', 'r4', 'I wish I could just go to sleep and never come back', 'crisis'),
    ('passive_ideation', 'r4', 'the only way out is to end it', 'crisis'),
    ('passive_ideation', 'r4', 'I think about killing myself most days', 'crisis'),
    ('passive_ideation', 'r4', "i've been wanting to die for weeks", 'crisis'),
    ('passive_ideation', 'r4', "I just don't want to be alive", 'crisis'),
    ('passive_ideation', 'r5', 'I wish I could stop waking up', 'crisis'),
    ('passive_ideation', 'r5', "I'd welcome death at this point", 'crisis'),
    ('passive_ideation', 'r5', 'sometimes I hope I get hit by a bus', 'crisis'),
    ('passive_ideation', 'r5', "I don't see any point in going on", 'crisis'),
    ('passive_ideation', 'r5', "I've been feeling like I'd be better off dead", 'crisis'),
    ('passive_ideation', 'r5', 'i really dont want to be here anymore guys', 'crisis'),
    ('passive_ideation', 'r5', 'I just want to sleep forever', 'crisis'),
    ('passive_ideation', 'r5', 'my existence is a burden and I want it to end', 'crisis'),
    ('passive_ideation', 'r5', "I'm thinking about ending things", 'crisis'),
    ('passive_ideation', 'r5', 'i keep wishing i would die in my sleep', 'crisis'),
    # ── means_plan_preparation ──
    ('means_plan_preparation', 'dev', 'I have pills saved up', 'crisis'),
    ('means_plan_preparation', 'dev', "I've been stockpiling my meds", 'crisis'),
    ('means_plan_preparation', 'dev', 'I bought a rope', 'crisis'),
    ('means_plan_preparation', 'dev', "I got a gun and I know what I'm going to do with it", 'crisis'),
    ('means_plan_preparation', 'dev', "I've picked a date", 'crisis'),
    ('means_plan_preparation', 'dev', "I've written letters to everyone saying goodbye", 'crisis'),
    ('means_plan_preparation', 'dev', "I'm giving my things away to friends", 'crisis'),
    ('means_plan_preparation', 'dev', 'I went to the bridge last night to see how high it is', 'crisis'),
    ('means_plan_preparation', 'dev', 'I looked up how to overdose', 'crisis'),
    ('means_plan_preparation', 'dev', "I've been saying goodbye to people", 'crisis'),
    ('means_plan_preparation', 'dev', 'I have a plan', 'crisis'),
    ('means_plan_preparation', 'r1', "I've been collecting my sleeping pills for a while now", 'crisis'),
    ('means_plan_preparation', 'r1', "there's a rope in my closet and I keep looking at it", 'crisis'),
    ('means_plan_preparation', 'r1', "I know which bridge I'd use", 'crisis'),
    ('means_plan_preparation', 'r1', "I've got enough pills to do it", 'crisis'),
    ('means_plan_preparation', 'r1', "I've decided how I'm going to do it", 'crisis'),
    ('means_plan_preparation', 'r1', 'I wrote a letter for my mum to find after', 'crisis'),
    ('means_plan_preparation', 'r1', "I gave my dog to my neighbour because I won't need to look after him", 'crisis'),
    ('means_plan_preparation', 'r1', "Tomorrow is the day, I've made up my mind", 'crisis'),
    ('means_plan_preparation', 'r1', "I've been researching how long it takes to bleed out", 'crisis'),
    ('means_plan_preparation', 'r1', "I have my dad's gun and I've been thinking about using it on myself", 'crisis'),
    ('means_plan_preparation', 'r2', 'I counted out all my pills tonight', 'crisis'),
    ('means_plan_preparation', 'r2', "I've been looking at the train tracks near my house a lot", 'crisis'),
    ('means_plan_preparation', 'r2', 'I bought a gun last week so I can end it', 'crisis'),
    ('means_plan_preparation', 'r2', 'I put my affairs in order and wrote my goodbye letter', 'crisis'),
    ('means_plan_preparation', 'r2', "I'm going to drive my car into a wall", 'crisis'),
    ('means_plan_preparation', 'r2', 'I filled the bathtub and have a razor ready', 'crisis'),
    ('means_plan_preparation', 'r2', "I'm on the roof right now", 'crisis'),
    ('means_plan_preparation', 'r3', "I have a bottle of pills next to my bed for when I'm ready", 'crisis'),
    ('means_plan_preparation', 'r3', "I've been standing at the edge of the platform when trains come", 'crisis'),
    ('means_plan_preparation', 'r3', 'I looked up how many sleeping pills would be lethal', 'crisis'),
    ('means_plan_preparation', 'r3', "I've decided tonight is when I do it", 'crisis'),
    ('means_plan_preparation', 'r3', 'I bought a rope today', 'crisis'),
    ('means_plan_preparation', 'r3', "I'm sitting in my car with the engine running in the garage", 'crisis'),
    ('means_plan_preparation', 'r3', "I'm writing my goodbye messages now", 'crisis'),
    ('means_plan_preparation', 'r4', 'I have enough tablets saved to end it', 'crisis'),
    ('means_plan_preparation', 'r4', 'I keep driving past the bridge thinking about jumping', 'crisis'),
    ('means_plan_preparation', 'r4', 'I wrote my will and goodbye notes last night', 'crisis'),
    ('means_plan_preparation', 'r4', "I'm going to take every pill in the house tonight", 'crisis'),
    ('means_plan_preparation', 'r4', "I've got the rope, I just need the courage", 'crisis'),
    ('means_plan_preparation', 'r4', "I'm going to step in front of a train", 'crisis'),
    ('means_plan_preparation', 'r4', "I'm at the top of the car park and I'm thinking of jumping", 'crisis'),
    ('means_plan_preparation', 'r4', 'I googled the most painless way to die', 'crisis'),
    ('means_plan_preparation', 'r5', 'I have a stash of pills in my room', 'crisis'),
    ('means_plan_preparation', 'r5', "I know exactly how I'd do it", 'crisis'),
    ('means_plan_preparation', 'r5', "I found the tallest building near me and I'm going there tonight", 'crisis'),
    ('means_plan_preparation', 'r5', "I've been holding my dad's gun and thinking about it", 'crisis'),
    ('means_plan_preparation', 'r5', "I'm going to slit my wrists", 'crisis'),
    ('means_plan_preparation', 'r5', 'the pills are in my hand right now', 'crisis'),
    ('means_plan_preparation', 'r5', "I've said goodbye to my family and I'm ready", 'crisis'),
    ('means_plan_preparation', 'r5', 'I just ordered something to end it with', 'crisis'),
    # ── recent_attempt ──
    ('recent_attempt', 'dev', 'I tried to overdose last night', 'crisis'),
    ('recent_attempt', 'dev', "I attempted last week and I'm still here", 'crisis'),
    ('recent_attempt', 'dev', 'I took too many pills yesterday on purpose', 'crisis'),
    ('recent_attempt', 'r1', 'I overdosed on Friday and they pumped my stomach', 'crisis'),
    ('recent_attempt', 'r1', 'I tried to hang myself a few days ago', 'crisis'),
    ('recent_attempt', 'r1', 'I just got out of hospital after trying to end things', 'crisis'),
    ('recent_attempt', 'r2', 'I tried to kill myself on Tuesday', 'crisis'),
    ('recent_attempt', 'r2', 'yesterday I swallowed a lot of pills but woke up', 'crisis'),
    ('recent_attempt', 'r2', 'I was in the ER last night after I hurt myself', 'crisis'),
    ('recent_attempt', 'r3', 'I attempted suicide two days ago', 'crisis'),
    ('recent_attempt', 'r3', 'last night I tried to drown myself', 'crisis'),
    ('recent_attempt', 'r4', 'I tried to end my life last month and I still feel the same', 'crisis'),
    ('recent_attempt', 'r4', 'I overdosed this morning', 'crisis'),
    ('recent_attempt', 'r5', "I tried to jump last night but couldn't", 'crisis'),
    ('recent_attempt', 'r5', 'I took an overdose on Sunday', 'crisis'),
    # ── self_harm_present_vs_past ──
    ('self_harm_present_vs_past', 'r1', 'I scratch my arms until they bleed', 'crisis'),
    ('self_harm_present_vs_past', 'r1', 'I relapsed and cut last night', 'crisis'),
    ('self_harm_present_vs_past', 'r1', 'cutting is the only thing that helps right now', 'crisis'),
    ('self_harm_present_vs_past', 'r1', "I haven't self harmed since 2019", 'concern'),
    ('self_harm_present_vs_past', 'r1', 'When I was 15 I used to burn myself', 'concern'),
    ('self_harm_present_vs_past', 'r1', 'I still have marks from when I used to cut', 'concern'),
    ('self_harm_present_vs_past', 'r2', 'I keep hitting myself when I get angry at myself', 'crisis'),
    ('self_harm_present_vs_past', 'r2', 'the urge to cut is so strong tonight', 'crisis'),
    ('self_harm_present_vs_past', 'r2', "I've been burning my skin with a lighter", 'crisis'),
    ('self_harm_present_vs_past', 'r2', 'I stopped cutting a few years ago', 'concern'),
    ('self_harm_present_vs_past', 'r2', 'I used to hurt myself when I was younger', 'concern'),
    ('self_harm_present_vs_past', 'r2', 'my scars are fading', 'concern'),
    ('self_harm_present_vs_past', 'r3', "I cut myself this morning and it won't stop bleeding", 'crisis'),
    ('self_harm_present_vs_past', 'r3', 'I want to cut so badly right now', 'crisis'),
    ('self_harm_present_vs_past', 'r3', 'I hurt myself on purpose sometimes', 'crisis'),
    ('self_harm_present_vs_past', 'r3', 'I cut myself a lot when I was a teenager but not anymore', 'concern'),
    ('self_harm_present_vs_past', 'r3', "I've been clean from cutting for six months", 'concern'),
    ('self_harm_present_vs_past', 'r4', "I've been cutting my thighs so nobody sees", 'crisis'),
    ('self_harm_present_vs_past', 'r4', 'I burned myself with a cigarette tonight', 'crisis'),
    ('self_harm_present_vs_past', 'r4', 'I punch walls until my knuckles bleed', 'crisis'),
    ('self_harm_present_vs_past', 'r4', "I self harmed when I was 16 but I'm okay now", 'concern'),
    ('self_harm_present_vs_past', 'r4', 'I have cutting scars on my arms from years ago', 'concern'),
    ('self_harm_present_vs_past', 'r5', "I've been hurting myself again", 'crisis'),
    ('self_harm_present_vs_past', 'r5', 'I cut deeper than usual last night', 'crisis'),
    ('self_harm_present_vs_past', 'r5', "I bang my head against the wall when I'm upset", 'crisis'),
    ('self_harm_present_vs_past', 'r5', "I haven't self-harmed in a long time", 'concern'),
    ('self_harm_present_vs_past', 'r5', 'I used to cut myself in my twenties', 'concern'),
    # ── harm_to_others ──
    ('harm_to_others', 'orig', "I'm going to kill my boss", 'crisis'),
    ('harm_to_others', 'orig', 'I want to hurt someone', 'crisis'),
    ('harm_to_others', 'orig', "I'm thinking about bringing a gun to school", 'crisis'),
    ('harm_to_others', 'orig', 'I want to shoot up my school', 'crisis'),
    ('harm_to_others', 'orig', 'I have urges to hurt my baby', 'crisis'),
    ('harm_to_others', 'dev', 'I am going to hurt someone at school tomorrow', 'crisis'),
    ('harm_to_others', 'dev', "I'm going to stab my roommate tonight", 'crisis'),
    ('harm_to_others', 'dev', 'I bought a knife for my stepfather', 'crisis'),
    ('harm_to_others', 'dev', "I'm planning to hurt the kids in my class", 'crisis'),
    ('harm_to_others', 'dev', "I want to make them all pay with my dad's gun", 'crisis'),
    ('harm_to_others', 'r1', "I'm going to bring my knife tomorrow and make him regret it", 'crisis'),
    ('harm_to_others', 'r1', 'I fantasize about hurting my stepmom', 'crisis'),
    ('harm_to_others', 'r1', "I'm going to run them over with my car", 'crisis'),
    ('harm_to_others', 'r1', 'I want everyone at my school to die', 'crisis'),
    ('harm_to_others', 'r2', "I'm going to poison my husband's food", 'crisis'),
    ('harm_to_others', 'r2', "I'm bringing a gun to work on Monday", 'crisis'),
    ('harm_to_others', 'r2', 'I want to hurt the people who bullied me, and I know where they live', 'crisis'),
    ('harm_to_others', 'r2', 'I think I could actually kill my father', 'crisis'),
    ('harm_to_others', 'r3', "I'm going to stab him when he gets home", 'crisis'),
    ('harm_to_others', 'r3', 'I have thought about poisoning my mother', 'crisis'),
    ('harm_to_others', 'r3', "I'm going to set the school on fire", 'crisis'),
    ('harm_to_others', 'r3', "one day I'll make them all pay with a gun", 'crisis'),
    ('harm_to_others', 'r4', "I'm going to kill my ex when I see him", 'crisis'),
    ('harm_to_others', 'r4', 'I keep imagining stabbing my coworker and it scares me', 'crisis'),
    ('harm_to_others', 'r4', "I'm going to hurt my little brother", 'crisis'),
    ('harm_to_others', 'r4', "I'm planning to shoot people at the mall", 'crisis'),
    ('harm_to_others', 'r5', "I'm going to strangle my roommate", 'crisis'),
    ('harm_to_others', 'r5', 'I want to hurt my teacher tomorrow', 'crisis'),
    ('harm_to_others', 'r5', 'I have a list of people I want to kill at school', 'crisis'),
    ('harm_to_others', 'r5', "I'm going to burn my ex's house down", 'crisis'),
    # ── abuse_danger ──
    ('abuse_danger', 'orig', 'My husband hits me when he drinks', 'crisis'),
    ('abuse_danger', 'orig', 'my boyfriend is hurting me', 'crisis'),
    ('abuse_danger', 'orig', "I'm not safe at home", 'crisis'),
    ('abuse_danger', 'orig', 'He threatened to kill me last night', 'crisis'),
    ('abuse_danger', 'orig', "my stepdad won't let me leave the house", 'crisis'),
    ('abuse_danger', 'orig', "I'm being abused", 'crisis'),
    ('abuse_danger', 'orig', 'he hit me again', 'crisis'),
    ('abuse_danger', 'dev', 'my partner choked me last night', 'crisis'),
    ('abuse_danger', 'dev', "I'm scared to go home, he'll hurt me", 'crisis'),
    ('abuse_danger', 'dev', 'my dad beats my mum and me', 'crisis'),
    ('abuse_danger', 'dev', "someone is touching me and I don't want it", 'crisis'),
    ('abuse_danger', 'dev', "I'm locked in my room and he won't let me out", 'crisis'),
    ('abuse_danger', 'r1', 'my boyfriend pushed me down the stairs yesterday', 'crisis'),
    ('abuse_danger', 'r1', "my uncle comes into my room at night and I'm scared", 'crisis'),
    ('abuse_danger', 'r1', "he says he'll kill me if I leave", 'crisis'),
    ('abuse_danger', 'r1', "my mum's partner hurts her and I'm scared he'll hurt me too", 'crisis'),
    ('abuse_danger', 'r1', "I'm hiding in the bathroom because he's angry and has a knife", 'crisis'),
    ('abuse_danger', 'r2', "my husband strangled me and said next time I'm dead", 'crisis'),
    ('abuse_danger', 'r2', 'my parents lock me in the basement', 'crisis'),
    ('abuse_danger', 'r2', "he won't let me have my phone or see anyone", 'crisis'),
    ('abuse_danger', 'r2', "my coach makes me do things I don't want to and says not to tell", 'crisis'),
    ('abuse_danger', 'r2', "I'm afraid to go to sleep because my stepdad gets violent", 'crisis'),
    ('abuse_danger', 'r3', "my girlfriend hits me and I can't leave", 'crisis'),
    ('abuse_danger', 'r3', "my dad punched me last night and I'm scared to go home", 'crisis'),
    ('abuse_danger', 'r3', 'my partner threatens to hurt me if I talk to my friends', 'crisis'),
    ('abuse_danger', 'r3', 'someone is following me home every night', 'crisis'),
    ('abuse_danger', 'r3', "my teacher touches me and says it's our secret", 'crisis'),
    ('abuse_danger', 'r4', "my husband beats me when he's drunk", 'crisis'),
    ('abuse_danger', 'r4', 'my step dad hits me and my sister every day', 'crisis'),
    ('abuse_danger', 'r4', "I'm scared my boyfriend will kill me", 'crisis'),
    ('abuse_danger', 'r4', 'my dad comes in my room at night and touches me', 'crisis'),
    ('abuse_danger', 'r4', "he took my phone and locked the door and won't let me out", 'crisis'),
    ('abuse_danger', 'r5', 'my partner keeps hitting me', 'crisis'),
    ('abuse_danger', 'r5', 'my father raped me again last night', 'crisis'),
    ('abuse_danger', 'r5', "my husband won't let me leave the house", 'crisis'),
    ('abuse_danger', 'r5', "I'm terrified he will find me and kill me", 'crisis'),
    ('abuse_danger', 'r5', 'my mom burns me with cigarettes', 'crisis'),
    # ── eating_substance ──
    ('eating_substance', 'dev', 'I took a whole bottle of tylenol', 'crisis'),
    ('eating_substance', 'dev', 'I drank until I passed out and took some pills', 'crisis'),
    ('eating_substance', 'dev', "I'm going to drink and take all my sleeping pills", 'crisis'),
    ('eating_substance', 'dev', "I've been throwing up after every meal", 'concern'),
    ('eating_substance', 'dev', "I haven't eaten anything in four days", 'concern'),
    ('eating_substance', 'dev', "I've been drinking way too much to get through the day", 'concern'),
    ('eating_substance', 'r1', 'I took half a bottle of paracetamol an hour ago', 'crisis'),
    ('eating_substance', 'r1', "I'm going to drink myself to death", 'crisis'),
    ('eating_substance', 'r1', "I've been making myself sick after I eat", 'concern'),
    ('eating_substance', 'r1', "I can't stop using and I'm scared", 'concern'),
    ('eating_substance', 'r2', 'I mixed my pills with vodka tonight', 'crisis'),
    ('eating_substance', 'r2', "I haven't eaten in a week and I don't want to", 'concern'),
    ('eating_substance', 'r2', "I've been using every day just to feel nothing", 'concern'),
    ('eating_substance', 'r2', 'I want to starve until I disappear', 'crisis'),
    ('eating_substance', 'r3', 'I took 30 of my sleeping pills', 'crisis'),
    ('eating_substance', 'r3', "I'm going to drink until I can't feel anything and take the rest of my meds", 'crisis'),
    ('eating_substance', 'r3', "I've been purging every day", 'concern'),
    ('eating_substance', 'r3', "I've been getting high every night to cope", 'concern'),
    ('eating_substance', 'r4', 'I swallowed a whole pack of paracetamol', 'crisis'),
    ('eating_substance', 'r4', "I'm going to drink a bottle of vodka with my pills", 'crisis'),
    ('eating_substance', 'r4', 'I make myself throw up every time I eat', 'concern'),
    ('eating_substance', 'r4', "I've been drinking every night to forget", 'concern'),
    ('eating_substance', 'r5', 'I swallowed all my antidepressants', 'crisis'),
    ('eating_substance', 'r5', "I've been starving myself for weeks", 'concern'),
    ('eating_substance', 'r5', 'I drink every morning just to function', 'concern'),
    # ── distress ──
    ('distress', 'orig', 'I feel so hopeless', 'concern'),
    ('distress', 'orig', "I can't go on like this", 'concern'),
    ('distress', 'orig', "I can't do this anymore", 'concern'),
    ('distress', 'orig', "what's the point of anything", 'concern'),
    ('distress', 'orig', 'I used to cut myself in high school', 'concern'),
    ('distress', 'orig', "I attempted suicide years ago and I'm doing better now", 'concern'),
    ('distress', 'orig', 'I hate myself', 'concern'),
    ('distress', 'orig', 'Nobody cares about me', 'concern'),
    ('distress', 'orig', 'I just want to disappear', 'concern'),
    ('distress', 'orig', "I've been having panic attacks every day", 'concern'),
    ('distress', 'orig', 'my dad used to hit me when I was a kid', 'concern'),
    ('distress', 'orig', 'My friend says she wants to kill herself', 'concern'),
    ('distress', 'orig', 'I feel like a burden to everyone', 'concern'),
    ('distress', 'orig', "I would never kill myself, but I'm exhausted", 'concern'),
    ('distress', 'orig', "I haven't eaten in days", 'concern'),
    ('distress', 'orig', 'I was abused as a child', 'concern'),
    ('distress', 'orig', 'In my story the hero kills himself', 'concern'),
    ('distress', 'orig', "I'm so depressed", 'concern'),
    ('distress', 'orig', 'I want the pain to stop', 'concern'),
    ('distress', 'orig', "I'm tired of everything", 'concern'),
    ('distress', 'dev', 'I burn myself when it gets bad', 'crisis'),
    ('distress', 'dev', "I've started cutting again", 'crisis'),
    ('distress', 'dev', 'I hurt myself last night on purpose', 'crisis'),
    ('distress', 'dev', 'I used to self harm years ago', 'concern'),
    ('distress', 'dev', 'I self-harmed as a teenager', 'concern'),
    ('distress', 'dev', 'I have old scars from cutting', 'concern'),
    ('distress', 'dev', "I haven't cut in two years", 'concern'),
    ('distress', 'dev', "I'm not okay", 'concern'),
    ('distress', 'dev', 'everything feels so dark lately', 'concern'),
    ('distress', 'dev', 'I feel empty all the time', 'concern'),
    ('distress', 'dev', "I can't keep going", 'concern'),
    ('distress', 'dev', "I'm so tired of fighting", 'concern'),
    ('distress', 'dev', "I'm a burden", 'concern'),
    ('distress', 'r1', "I feel like I'm sinking and nobody sees it", 'concern'),
    ('distress', 'r1', "I don't know how much longer I can do this", 'concern'),
    ('distress', 'r1', "everything hurts and I'm so tired", 'concern'),
    ('distress', 'r1', "I'm struggling a lot right now", 'concern'),
    ('distress', 'r1', 'I feel completely alone', 'concern'),
    ('distress', 'r1', "I can't handle this anymore", 'concern'),
    ('distress', 'r2', "I'm barely holding on", 'concern'),
    ('distress', 'r2', 'I feel like a burden to my family', 'concern'),
    ('distress', 'r2', 'I cry myself to sleep every night', 'concern'),
    ('distress', 'r2', "I don't see things getting better", 'concern'),
    ('distress', 'r2', 'I feel worthless', 'concern'),
    ('distress', 'r2', "I'm exhausted all the time and nothing matters", 'concern'),
    ('distress', 'r3', "I don't feel like myself and everything feels heavy", 'concern'),
    ('distress', 'r3', 'I feel like nobody would care either way', 'concern'),
    ('distress', 'r3', "I can't cope with anything anymore", 'concern'),
    ('distress', 'r3', 'I feel so trapped', 'concern'),
    ('distress', 'r3', "there's no hope for me", 'concern'),
    ('distress', 'r3', 'I hate my life', 'concern'),
    ('distress', 'r4', "I'm really not doing well", 'concern'),
    ('distress', 'r4', "I feel like I'm falling apart", 'concern'),
    ('distress', 'r4', "I can't go on anymore", 'concern'),
    ('distress', 'r4', 'nothing ever gets better for me', 'concern'),
    ('distress', 'r4', "I'm so lonely it hurts", 'concern'),
    ('distress', 'r4', 'I feel numb all the time', 'concern'),
    ('distress', 'r4', 'i honestly hate myself', 'concern'),
    ('distress', 'r4', "I don't know how to keep going", 'concern'),
    ('distress', 'r5', "I feel like I'm drowning in everything", 'concern'),
    ('distress', 'r5', "I'm tired of trying so hard for nothing", 'concern'),
    ('distress', 'r5', 'I feel invisible to everyone', 'concern'),
    ('distress', 'r5', "I've been crying all day", 'concern'),
    ('distress', 'r5', "I don't think anyone would notice if I disappeared", 'crisis'),
    ('distress', 'r5', "I'm completely broken", 'concern'),
    ('distress', 'r5', "everything is falling apart and I can't cope", 'concern'),
    # ── negatives ──
    ('negatives', 'orig', 'this story kills it', 'none'),
    ('negatives', 'orig', "I'm dying to know what happens next", 'none'),
    ('negatives', 'orig', 'kill the lights and light a candle', 'none'),
    ('negatives', 'orig', "I'm dead tired after work", 'none'),
    ('negatives', 'orig', 'I kill the dragon with my sword', 'none'),
    ('negatives', 'orig', 'my mom is going to kill me when she sees my grades', 'none'),
    ('negatives', 'orig', 'I could kill for a coffee right now', 'none'),
    ('negatives', 'orig', 'That joke killed me', 'none'),
    ('negatives', 'orig', 'I cut myself shaving this morning', 'none'),
    ('negatives', 'orig', 'I accidentally cut myself while cooking', 'none'),
    ('negatives', 'orig', "I'm not suicidal, just bored", 'concern'),
    ('negatives', 'orig', "I don't want to die in this cave, I run for the exit", 'none'),
    ('negatives', 'orig', 'I jump off the bridge into the river to escape the guards', 'none'),
    ('negatives', 'orig', 'My phone is dead', 'none'),
    ('negatives', 'orig', 'I want to take my life back', 'none'),
    ('negatives', 'orig', "You're killing it with these scenes", 'none'),
    ('negatives', 'orig', "I'd die for a slice of pizza", 'none'),
    ('negatives', 'orig', 'I need to cut myself some slack', 'none'),
    ('negatives', 'orig', "I don't want to wake up early tomorrow", 'none'),
    ('negatives', 'orig', "I don't want to live in a big city", 'none'),
    ('negatives', 'orig', "I'm dying of laughter", 'none'),
    ('negatives', 'orig', "Let's kill some time before the train", 'none'),
    ('negatives', 'orig', 'I want to become someone who can lead', 'none'),
    ('negatives', 'orig', "my grandmother's kitchen and the smell of bread", 'none'),
    ('negatives', 'orig', "He never hits me, he's gentle", 'none'),
    ('negatives', 'orig', 'I ran 5 kms this morning', 'none'),
    ('negatives', 'orig', 'Suicide Squad was a terrible movie', 'none'),
    ('negatives', 'orig', 'My back is killing me', 'none'),
    ('negatives', 'orig', 'I want to kill it at my interview', 'none'),
    ('negatives', 'orig', 'I stab the shadow and it dissolves', 'none'),
    ('negatives', 'orig', 'the old knight wants to die in battle', 'none'),
    ('negatives', 'orig', "I'm going to shoot my shot with her", 'none'),
    ('negatives', 'orig', "I'd rather die than miss this concert lol", 'none'),
    ('negatives', 'orig', 'I want to upskill myself this year', 'none'),
    ('negatives', 'orig', 'spiders, deep water, hospitals', 'none'),
    ('negatives', 'orig', 'suicide, self-harm', 'none'),
    ('negatives', 'orig', '', 'none'),
    ('negatives', 'dev', 'I took the pills the healer gave me in the story', 'none'),
    ('negatives', 'dev', 'I bought a rope for climbing', 'none'),
    ('negatives', 'dev', 'I have a plan for my career', 'none'),
    ('negatives', 'dev', 'I said goodbye to my friends at the airport', 'none'),
    ('negatives', 'dev', "I'm giving away my old clothes", 'none'),
    ('negatives', 'dev', 'the knight throws himself off the cliff', 'none'),
    ('negatives', 'dev', 'I jump from the cliff into the lake', 'none'),
    ('negatives', 'dev', "I'm going to destroy them in this game", 'none'),
    ('negatives', 'dev', "I'll kill the lights now", 'none'),
    ('negatives', 'dev', 'we picked a date for the wedding', 'none'),
    ('negatives', 'dev', 'my plan is to sleep all weekend', 'none'),
    ('negatives', 'dev', "I'm dying of boredom", 'none'),
    ('negatives', 'dev', 'I want to disappear into a good book', 'none'),
    ('negatives', 'dev', "I wish I wasn't here at this boring meeting", 'none'),
    ('negatives', 'dev', 'the point of living is to love', 'none'),
    ('negatives', 'r1', "I'm dying to see how this ends", 'none'),
    ('negatives', 'r1', 'I nearly died laughing at that scene', 'none'),
    ('negatives', 'r1', 'The wizard swallows a handful of glowing berries', 'none'),
    ('negatives', 'r1', 'I tie the rope to the tree and climb down into the gorge', 'none'),
    ('negatives', 'r1', 'I drank some wine and took my vitamins', 'none'),
    ('negatives', 'r1', 'my plan is to finish this chapter tonight', 'none'),
    ('negatives', 'r1', "I'm going to murder this exam", 'none'),
    ('negatives', 'r1', "I'd kill for a holiday", 'none'),
    ('negatives', 'r1', 'my villain wants to end the world', 'none'),
    ('negatives', 'r1', 'I say goodbye to the innkeeper and leave town', 'none'),
    ('negatives', 'r1', 'the queen poisons the king in act three', 'none'),
    ('negatives', 'r1', 'my cat died last year and I still miss her', 'none'),
    ('negatives', 'r1', 'this level is so hard I want to throw my controller', 'none'),
    ('negatives', 'r1', 'I gave my old bike to my cousin', 'none'),
    ('negatives', 'r1', 'I want to disappear for a weekend in the mountains', 'none'),
    ('negatives', 'r1', 'I feel so alive when I write', 'none'),
    ('negatives', 'r1', 'The bridge was so high the view was amazing', 'none'),
    ('negatives', 'r1', 'I shoot the arrow at the target', 'none'),
    ('negatives', 'r1', "I'm not going to give up on my dreams", 'none'),
    ('negatives', 'r1', 'my grandpa passed away peacefully in his sleep', 'none'),
    ('negatives', 'r2', "I'm ready to go to the party", 'none'),
    ('negatives', 'r2', 'I counted the coins in my purse', 'none'),
    ('negatives', 'r2', 'I hold the rope tight as the ship rocks', 'none'),
    ('negatives', 'r2', 'we picked the date for our trip', 'none'),
    ('negatives', 'r2', 'my grandmother left me her ring in her will', 'none'),
    ('negatives', 'r2', 'I said my goodbyes to my coworkers on my last day', 'none'),
    ('negatives', 'r2', "I'm on the roof fixing the gutter", 'none'),
    ('negatives', 'r2', 'the dragon breathes fire and I hide behind the rock', 'none'),
    ('negatives', 'r2', 'I want to poison the rats in the barn', 'none'),
    ('negatives', 'r2', 'I gave my old phone to my sister', 'none'),
    ('negatives', 'r2', 'I keep looking at the stars', 'none'),
    ('negatives', 'r2', "I'm going to destroy my opponent in chess", 'none'),
    ('negatives', 'r2', 'I died in the game three times', 'none'),
    ('negatives', 'r2', "Please don't kill off my favourite character", 'none'),
    ('negatives', 'r2', "I'm finished with this chapter", 'none'),
    ('negatives', 'r2', 'I cut the cake and handed out slices', 'none'),
    ('negatives', 'r2', "I'm so tired of this rain", 'none'),
    ('negatives', 'r2', 'my pills are in the kitchen cabinet', 'none'),
    ('negatives', 'r2', 'the hero takes all the potions at once', 'none'),
    ('negatives', 'r2', 'I bought a knife set for my new kitchen', 'none'),
    ('negatives', 'r2', 'I feel alive for the first time in years', 'none'),
    ('negatives', 'r2', "I'm hiding the presents from my kids", 'none'),
    ('negatives', 'r2', "my brother is so angry he's got a point", 'none'),
    ('negatives', 'r2', "I can't wait to see what happens", 'none'),
    ('negatives', 'r2', 'I have a plan to visit Japan next year', 'none'),
    ('negatives', 'r3', 'I want to dye my hair purple', 'none'),
    ('negatives', 'r3', 'this movie is to die for', 'none'),
    ('negatives', 'r3', "I'm dead serious about learning piano", 'none'),
    ('negatives', 'r3', 'I nearly died when I saw the price', 'none'),
    ('negatives', 'r3', 'Our plants all died while we were away', 'none'),
    ('negatives', 'r3', 'I hope I live to be a hundred', 'none'),
    ('negatives', 'r3', "I'm tired but happy", 'none'),
    ('negatives', 'r3', "I'm going to kill this workout", 'none'),
    ('negatives', 'r3', 'my brother kills me with his jokes', 'none'),
    ('negatives', 'r3', 'I keep my medication in a pill organiser', 'none'),
    ('negatives', 'r3', 'I take my pills every morning with breakfast', 'none'),
    ('negatives', 'r3', 'We went to the bridge to watch the fireworks', 'none'),
    ('negatives', 'r3', 'I stand on the cliff edge and watch the sunset', 'none'),
    ('negatives', 'r3', "the assassin poisons the duke's wine", 'none'),
    ('negatives', 'r3', 'In the game I died to the final boss again', 'none'),
    ('negatives', 'r3', 'I want to end this chapter on a hopeful note', 'none'),
    ('negatives', 'r3', "I'm writing a goodbye scene for my novel", 'none'),
    ('negatives', 'r3', 'my plan is to quit sugar', 'none'),
    ('negatives', 'r3', 'I bought rope for the garden swing', 'none'),
    ('negatives', 'r3', "I'm going to shoot some photos at the lake", 'none'),
    ('negatives', 'r3', 'I said goodbye to my old flat', 'none'),
    ('negatives', 'r3', "I'm ready to start a new chapter", 'none'),
    ('negatives', 'r3', "my friend's dog died and I'm helping her through it", 'none'),
    ('negatives', 'r3', 'I gave away my old books to the library', 'none'),
    ('negatives', 'r3', 'I hit my head on the cupboard', 'none'),
    ('negatives', 'r3', 'The knight swears to kill the dragon by dawn', 'none'),
    ('negatives', 'r3', 'my sister will kill me for eating her cake', 'none'),
    ('negatives', 'r3', "I'd rather be dead than wear that outfit lol", 'none'),
    ('negatives', 'r3', "I'm on the train right now", 'none'),
    ('negatives', 'r3', 'I love my life right now', 'none'),
    ('negatives', 'r3', "I'm not suicidal, I promise, just sad sometimes", 'concern'),
    ('negatives', 'r3', 'I fell off my bike and hurt myself', 'none'),
    ('negatives', 'r4', "lol i'm dead 😂", 'none'),
    ('negatives', 'r4', 'I could die happy after that concert', 'none'),
    ('negatives', 'r4', "I'm literally dying here, this heat", 'none'),
    ('negatives', 'r4', 'my laptop died during the exam', 'none'),
    ('negatives', 'r4', 'I killed it at karaoke', 'none'),
    ('negatives', 'r4', 'we buried our hamster today', 'none'),
    ('negatives', 'r4', 'I want to die my hair red', 'none'),
    ('negatives', 'r4', 'the knight falls on his sword', 'none'),
    ('negatives', 'r4', 'I throw myself into my work', 'none'),
    ('negatives', 'r4', "I'm going to jump into the pool", 'none'),
    ('negatives', 'r4', "I'm hanging out with friends tonight", 'none'),
    ('negatives', 'r4', 'I hang the painting in the hallway', 'none'),
    ('negatives', 'r4', "I'm cutting back on coffee", 'none'),
    ('negatives', 'r4', 'I cut my hair short', 'none'),
    ('negatives', 'r4', "I'm going to kill the spider in the bathroom", 'none'),
    ('negatives', 'r4', "my mum will murder me if I'm late", 'none'),
    ('negatives', 'r4', 'I hope my plants survive the winter', 'none'),
    ('negatives', 'r4', 'I keep my sleeping pills in the drawer', 'none'),
    ('negatives', 'r4', 'I bought a gun safe', 'none'),
    ('negatives', 'r4', 'I want to disappear from social media for a bit', 'none'),
    ('negatives', 'r4', 'I said my goodbyes to the old house', 'none'),
    ('negatives', 'r4', "I'm ready to give up coffee", 'none'),
    ('negatives', 'r4', 'I take the potion and drink it all', 'none'),
    ('negatives', 'r4', 'I want to end my shift early', 'none'),
    ('negatives', 'r4', "I'm writing a story about a character who wants to die", 'concern'),
    ('negatives', 'r4', 'the villain threatens to kill me in the story', 'none'),
    ('negatives', 'r4', "I'm going to shoot my shot at the audition", 'none'),
    ('negatives', 'r4', "I'm planning a surprise for my wife", 'none'),
    ('negatives', 'r4', 'I am attempting a marathon next year', 'none'),
    ('negatives', 'r4', 'my grandad passed last year and I miss him', 'none'),
    ('negatives', 'r4', "I'm afraid of spiders", 'none'),
    ('negatives', 'r4', "I'm scared of the dark", 'none'),
    ('negatives', 'r4', 'my brother keeps following me around the house', 'none'),
    ('negatives', 'r4', "I gave my cat to my sister because I'm moving abroad", 'none'),
    ('negatives', 'r5', 'I could just die of happiness', 'none'),
    ('negatives', 'r5', 'this assignment is killing me', 'none'),
    ('negatives', 'r5', "I'm going to kill my presentation tomorrow", 'none'),
    ('negatives', 'r5', 'the old man dies peacefully in the epilogue', 'none'),
    ('negatives', 'r5', 'I died laughing at your message', 'none'),
    ('negatives', 'r5', 'I want to jump into the ocean this summer', 'none'),
    ('negatives', 'r5', 'My pills help me sleep better', 'none'),
    ('negatives', 'r5', 'I bought a pistol-shaped water gun for my son', 'none'),
    ('negatives', 'r5', 'I said goodbye to my mum at the station', 'none'),
    ('negatives', 'r5', 'I gave away some old toys to charity', 'none'),
    ('negatives', 'r5', "I'm ready to move on from my old job", 'none'),
    ('negatives', 'r5', "I can't wait to sleep in tomorrow", 'none'),
    ('negatives', 'r5', 'I hope I never wake up this early again', 'none'),
    ('negatives', 'r5', 'I want to disappear into the forest for a hike', 'none'),
    ('negatives', 'r5', "I'm writing a letter to my future self", 'none'),
    ('negatives', 'r5', 'I picked a date for my birthday party', 'none'),
    ('negatives', 'r5', 'the bridge in my dream was beautiful', 'none'),
    ('negatives', 'r5', 'I keep a knife in my backpack for camping', 'none'),
    ('negatives', 'r5', "I'm sitting in my car waiting for my friend", 'none'),
    ('negatives', 'r5', "my boss is going to kill me if I'm late again", 'none'),
    ('negatives', 'r5', 'he hit me with a snowball', 'none'),
    ('negatives', 'r5', 'my sister pushed me into the pool', 'none'),
    ('negatives', 'r5', "I'm afraid I'll fail my exam", 'none'),
    ('negatives', 'r5', "I'd rather die than eat olives", 'none'),
    ('negatives', 'r5', "I'm dying for a holiday", 'none'),
    ('negatives', 'r5', 'I keep thinking about my ex', 'none'),
    ('negatives', 'r5', 'I killed the boss on my first try', 'none'),
    ('negatives', 'r5', 'I stab at the shadows with my torch', 'none'),
    ('negatives', 'r5', 'I want to end the day with a walk', 'none'),
    ('negatives', 'r5', 'my kids would be lost without me', 'none'),
    ('negatives', 'r5', "I'm done with this job", 'none'),
]

# Rows deliberately NOT asserted: genuinely ambiguous, printed for visibility.
KNOWN_MISSES: list[tuple[str, str, str, str]] = [
    ('means_plan_preparation', 'r2', "I've planned it for Saturday", 'crisis'),  # could be a party; screened as none
]

_RANK = {"none": 0, "concern": 1, "crisis": 2}


def _metrics(rows):
    """rows: (text, label, predicted)."""
    out = {}
    for cls in ("crisis", "concern", "none"):
        tp = sum(1 for _, y, p in rows if y == cls and p == cls)
        fp = sum(1 for _, y, p in rows if y != cls and p == cls)
        fn = sum(1 for _, y, p in rows if y == cls and p != cls)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        out[cls] = (prec, rec, tp + fn)
    return out


def test_table_shape():
    assert len(TABLE) >= 60
    texts = [t for _, _, t, _ in TABLE]
    assert len(set(texts)) == len(texts)
    cats = {c for c, _, _, _ in TABLE}
    assert cats == {"suicide_self_harm_explicit", "passive_ideation", "means_plan_preparation",
                    "recent_attempt", "self_harm_present_vs_past", "harm_to_others",
                    "abuse_danger", "eating_substance", "distress", "negatives"}
    for c in cats:
        assert sum(1 for cc, _, _, _ in TABLE if cc == c) >= 10, c


def test_table_precision_recall(capsys):
    rows = [(t, y, screen_text(t).level) for _, _, t, y in TABLE]
    m = _metrics(rows)
    wrong = [(t, y, p) for t, y, p in rows if y != p]
    with capsys.disabled():
        print(f"\n[safety table] n={len(rows)} accuracy="
              f"{1 - len(wrong) / len(rows):.3f}")
        for cls, (p, r, n) in m.items():
            print(f"  {cls:8s} n={n:3d} precision={p:.3f} recall={r:.3f}")
        by_cat: dict[str, list[int]] = {}
        for (c, _, _, _), (_, y, p) in zip(TABLE, rows):
            by_cat.setdefault(c, [0, 0])
            by_cat[c][0] += 1
            by_cat[c][1] += int(y == p)
        for c, (n, ok) in by_cat.items():
            print(f"    {c:28s} n={n:3d} correct={ok}")
        for t, y, p in wrong:
            print(f"  MISS label={y:7s} got={p:7s} :: {t!r}")
        for _, src, t, y in KNOWN_MISSES:
            print(f"  KNOWN (not asserted, {src}) label={y} got={screen_text(t).level} :: {t!r}")
    assert m["crisis"][1] == 1.0, [w for w in wrong if w[1] == "crisis"]
    assert not [t for t, y, p in rows if y == "none" and p == "crisis"]
    # Concern-level misses are tolerated but bounded.
    assert len(wrong) <= len(rows) // 10


@pytest.mark.parametrize("text,label", [(t, y) for _, _, t, y in TABLE if y == "crisis"])
def test_every_crisis_row(text, label):
    res = screen_text(text)
    assert res.level == "crisis"
    assert res.message and res.resources and res.reasons


def test_result_shape_and_no_text_leak():
    secret = "I want to kill myself because of Bartholomew"
    res = screen_text(secret)
    dumped = res.model_dump_json()
    assert "Bartholomew" not in dumped and "kill myself" not in dumped
    d = res.model_dump()
    assert set(d) == {"level", "reasons", "message", "resources"}
    for r in d["resources"]:
        assert {"name", "url", "note"} <= set(r)
    none = screen_text("a walk by the sea")
    assert none.level == "none" and none.message is None and none.resources == []


def test_resources_and_disclaimer():
    urls = [r.url for r in RESOURCES]
    assert "https://findahelpline.com" in urls
    assert any("emergency" in r.note.lower() for r in RESOURCES)
    assert "not therapy" in DISCLAIMER and "18+" in DISCLAIMER
    gb = resources_for("GB")
    assert gb[0].name == "Emergency services" and gb[1].name == "Samaritans"
    assert any(r.url == "https://findahelpline.com" for r in gb)


def test_normalise_tricks():
    assert "kill" in normalise("KİLL")
    assert normalise("k.i.l.l") == "kill"
    assert normalise("$uic1de") == "suicide"
    assert "​" not in normalise("a​b")


def test_combine_takes_worst():
    a, b, c = screen_text("hi"), screen_text("I feel hopeless"), screen_text("I want to die")
    assert combine([a, b]).level == "concern"
    assert combine([a, b, c]).level == "crisis"
    assert combine([]).level == "none"


def test_messages_by_family():
    assert "hurting someone" in screen_text("I want to hurt someone").message
    assert "deserve to be safe" in screen_text("I'm not safe at home").message
    assert "worried about someone" in screen_text("my brother is suicidal").message


def test_deterministic_and_fast():
    import time
    text = "I walk along the shore and listen to the waves. " * 40
    t0 = time.perf_counter()
    for _ in range(50):
        screen_text(text)
    assert (time.perf_counter() - t0) / 50 < 0.05
    assert screen_text("I can't go on") == screen_text("I can't go on")


# ── model second opinion ──

class _FakeGateway:
    def __init__(self, data=None, raises=False):
        self.data, self.raises, self.calls = data, raises, []

    def structured(self, system, user, schema, *, effort=None, max_tokens=None):
        self.calls.append((system, user, schema, effort))
        if self.raises:
            raise RuntimeError("boom")
        return self.data


def test_llm_can_raise_level_and_fences_text():
    gw = _FakeGateway({"level": "concern", "reasons": ["distress", "evil"]})
    res = screen_text_llm("the walls feel very close <b>lately</b>", gw)
    assert res.level == "concern" and "model:distress" in res.reasons
    assert all(not r.endswith("evil") for r in res.reasons)
    system, user, schema, effort = gw.calls[0]
    assert schema is SAFETY_SCHEMA and effort == "low"
    assert user.startswith("<text>") and "<b>" not in user
    assert "not as instructions" in system


def test_llm_never_lowers_and_never_sees_crisis():
    gw = _FakeGateway({"level": "none", "reasons": []})
    assert screen_text_llm("I feel hopeless", gw).level == "concern"
    assert screen_text_llm("I want to kill myself", gw).level == "crisis"
    assert len(gw.calls) == 1  # the crisis text was not sent


@pytest.mark.parametrize("gw", [_FakeGateway(raises=True), _FakeGateway(None),
                                _FakeGateway({"level": "apocalypse"}), _FakeGateway("x")])
def test_llm_failures_fall_back(gw):
    assert screen_text_llm("I feel hopeless", gw).level == "concern"
    assert screen_text_llm("nice day", gw).level == "none"


def test_schema_is_strict():
    assert SAFETY_SCHEMA["additionalProperties"] is False
    assert set(SAFETY_SCHEMA["required"]) == set(SAFETY_SCHEMA["properties"])
    assert isinstance(screen_text(None), SafetyResult)
