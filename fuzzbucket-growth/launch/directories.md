# Fuzzbucket: AI tool directories and launch sites

Prepared 2026-10-21. Nothing here has been submitted. This is a working list for the launch team.

## Read first: accuracy rules for every listing

Product facts checked on fuzzbucket.ai, /pricing, /affiliate, /startups, /mcp and /resources/changelog on 2026-10-21:

- **Pricing model: Paid.** Plans are Starter $19/mo (260 credits, up to 2 brands), Pro $49/mo (680 credits, up to 5 brands) and Studio $149/mo (2,100 credits, unlimited brands). Yearly billing saves about 16.7% (2 months). In every directory pricing field, choose **"Paid"**, not "Free", "Freemium" or "Free trial". Never call Fuzzbucket free in any copy.
- **Publishing is Meta only:** Facebook Pages, Instagram accounts and Threads profiles. Do not claim LinkedIn, X or TikTok publishing, team roles or white-label.
- **Sign-in:** email, Google or Apple.
- **MCP server:** `https://api.fuzzbucket.ai/mcp` (OAuth). It works with Claude, ChatGPT, Claude Code, Cursor, GitHub Copilot (VS Code), Gemini CLI and Grok.
- **Other programs:** a Startups program (25% off eligible subscription charges for 12 months, up to $1,000) and an affiliate program (20% recurring for 12 months, run through Rekomi).
- **Do not use the sample numbers from the homepage mockups,** such as "128.4K reach", as results.

> Note for the coordinator: the site's bundled pricing strings still contain a "$0 Free" plan entry ("One free Brand Brain setup", "Creation unlocks with a paid plan"). Following your instruction, this pack treats the product as **Paid** everywhere. If product confirms that Brand Brain setup costs nothing, we can add "Brand Brain setup at no cost" later.

**What the "Verified" column means.** On 2026-10-21 we sent a scripted HTTP check (browser user-agent) to every submission URL.
- `200`: the page loaded.
- `200 (login)`: the URL redirects to a sign-in page, so the route exists and needs an account.
- `403 bot-wall`: Cloudflare or bot protection blocked the script. The site is real and widely documented, but someone needs to open it in a browser before relying on it.
- Routes that are dead or changed are listed at the bottom.

**What "Traffic tier" means.** It is a rough estimate from public reputation and third-party roundups, not measured data.
- **A:** millions of visits a month
- **B:** hundreds of thousands
- **C:** tens of thousands
- **D:** small or niche

Use it to prioritise, not to forecast.

**What "Cost to list" means.** It is the price the site states, or a third-party report where noted. Prices change often, so confirm at submission.

---

## Copy bank (character counts checked by script)

Use the copy set named in each row's **Copy** column. Shorten or lengthen to fit each form, but keep the facts unchanged.

### Copy set A: general AI directories
- **Tagline (51):** Your brand's AI marketing team, built from one link
- **Short description (156):** Fuzzbucket learns your brand from your site and socials, plans campaigns, makes on-brand visuals and copy, and schedules to Instagram, Facebook and Threads.
- **Long description (461):** Fuzzbucket is an AI marketing workspace for agencies, creators and lean teams. Paste a website and social links and it builds a Brand Brain: voice, audience, products and goals. From there it maps campaign strategy (goals, KPIs, channel lanes), generates on-brand images, video, copy variants and campaign packs on a shared canvas, schedules posts to Facebook Pages, Instagram and Threads, and reads Meta analytics to shape the next campaign. Plans from $19/mo.
- **Categories:** AI Marketing, Social Media Management, Content Generation, AI Image Generator, AI Video Generator, Copywriting, Marketing Automation
- **Tags:** brand voice, campaign planning, social media scheduling, Instagram, Threads, Facebook, agencies, creators
- **Pricing field:** Paid. From $19/mo, yearly billing saves about 16.7% (2 months).

### Copy set B: launch platforms and indie communities (story-led)
- **Tagline (48):** Turn a brand link into a scheduled Meta campaign
- **Short description (149):** Brand Brain + campaign planner + image/video/copy studio + Meta scheduler in one workspace. Built for agencies and creators juggling multiple brands.
- **Long description (471):** Most AI content tools forget your brand the moment you close the tab. Fuzzbucket keeps a reusable Brand Brain per brand (Starter: 2, Pro: 5, Studio: unlimited), so every campaign plan, caption, image and video starts on-voice. Plan the campaign, generate the assets on a canvas with top image, video and voice models, schedule to Facebook, Instagram and Threads, then let Meta performance data feed the next round. It also works inside Claude, ChatGPT and Cursor via MCP.
- **Categories:** Marketing, Artificial Intelligence, Social Media, SaaS, Productivity, Design Tools
- **Pricing field:** Paid

### Copy set M: MCP server directories
- **Tagline (54):** Brand-aware marketing generation inside any MCP client
- **Short description (159):** Fuzzbucket's MCP server lets Claude, ChatGPT, Cursor and other clients read your Brand Brain, draft on-brand assets and run image/video generations with OAuth.
- **Long description (483):** Connect https://api.fuzzbucket.ai/mcp with OAuth and your AI assistant can list your brands, read stored brand context, open Fuzzbucket workspaces, create and edit canvas drafts, chat with the Fuzzbucket orchestrator, run image, video, audio and text generations with your plan's credits, and preview or download results without leaving the conversation. Works with Claude, ChatGPT, Claude Code, Cursor, VS Code Copilot, Gemini CLI and Grok. Requires a Fuzzbucket plan (from $19/mo).
- **Categories:** Marketing, Image Generation, Video Generation, Content Creation, Remote MCP, OAuth
- **Server URL:** https://api.fuzzbucket.ai/mcp · **Docs:** https://fuzzbucket.ai/mcp

### Copy set R: software review and comparison sites
- Use set A copy.
- **Positioning line for "alternatives to" fields:** "An alternative to stitching together Canva + ChatGPT + Buffer/Later for Meta channels: one workspace that remembers each brand."
- **Competitor tags to request:** Predis.ai, Ocoya, Blaze, Jasper, Later, Buffer, Hootsuite, Canva Magic Studio.

### Assets every form will ask for
- **Logo:** 512x512 PNG and SVG
- **Screenshots:** 1270x760, plus 4 at 16:9. Show the Brand Brain, strategy map, canvas, calendar and analytics.
- **Video:** a 60–90 second demo
- **Contact email:** a founder email at fuzzbucket.ai
- **Socials:** x.com/FuzzbucketAI, instagram.com/fuzzbucketai, reddit.com/r/FuzzbucketAI, discord.gg/DnvTg4qa53
- **Company:** "Fuzzbucket (built by dkilo)"
- **Launch date:** set to match the Product Hunt date for launch-style sites

### UTM convention
Append `?utm_source=<directory-slug>&utm_medium=directory&utm_campaign=launch26` to the website URL wherever the form allows it. Without UTMs, signups cannot be attributed to a directory.

---

## Master list (75 entries)

Columns: **#**, **Directory**, **Site**, **Submission URL**, **Verified** (2026-10-21), **Cost to list**, **Traffic tier**, **Copy**, **Priority** (P1 = this week, P2 = launch week, P3 = backlog).

### Tier 1: high-traffic launch and AI directories

| # | Directory | Site | Submission URL | Verified | Cost to list | Traffic | Copy | Priority |
|---|---|---|---|---|---|---|---|---|
| 1 | Product Hunt | producthunt.com | https://www.producthunt.com/posts/new | 403 bot-wall | Free to launch | A | PH kit (see product-hunt.md) | P1 (schedule) |
| 2 | There's An AI For That | theresanaiforthat.com | https://theresanaiforthat.com/launch/ | 200 | $49 one-time basic review; $437 "Maximum Exposure" (includes newsletter). Free route: monthly indie thread on X, one tool picked per thread | A | A | P1 ($49 minimum) |
| 3 | Futurepedia | futurepedia.io | No public new-tool form; old /submit-tool returns 404. Business enquiries: https://www.futurepedia.io/business (contact@futurepedia.io) | 200 (business page) | Paid "enhanced/verified" listing, about $497 per third-party reports (SaaSCity, Oct 2026) | A | A | P2 (email first) |
| 4 | Toolify | toolify.ai | https://www.toolify.ai/submit | 403 bot-wall | Paid listing per third-party reports (2026); confirm price on page | A | A | P1 |
| 5 | AlternativeTo | alternativeto.net | https://alternativeto.net/manage-item/ (sign in, then "Add application") | 403 bot-wall | Free | A | R | P1 |
| 6 | G2 | g2.com | https://sell.g2.com/ (claim/create a free profile) | 200 | Free profile; paid upgrades | A | R | P2 |
| 7 | Capterra / GetApp / Software Advice (Gartner Digital Markets) | capterra.com | https://www.capterra.com/vendors/ | 200 | Free basic listing; PPC is paid | A | R | P2 |
| 8 | Hacker News (Show HN) | news.ycombinator.com | https://news.ycombinator.com/submit | 200 | Free | A | Plain-text founder post; the MCP angle fits HN best | P3 (only with a technical angle) |
| 9 | Crunchbase | crunchbase.com | https://www.crunchbase.com/add-new | 403 bot-wall | Free | A | A (company profile) | P2 |
| 10 | SourceForge (business software) | sourceforge.net | https://sourceforge.net/software/vendors/ | 403 bot-wall | Free basic listing | A | R | P3 |
| 11 | The Rundown AI Supertools | therundown.ai | https://www.therundown.ai/submit (the old supertools.therundown.ai/submit redirects here) | 200 | Check on page | B | A | P1 |
| 12 | Future Tools (Matt Wolfe) | futuretools.io | https://www.futuretools.io/submit-a-tool | 200 | Free, manual review | B | A | P1 |
| 13 | TopAI.tools | topai.tools | https://topai.tools/submit | 403 bot-wall | Paid only (Fast Track/Featured; refunded if rejected) per third-party reports | B | A | P2 |
| 14 | BetaList | betalist.com | https://betalist.com/submit | 200 | Free (long queue); paid expedite | B | B | P1 |
| 15 | SaaSHub | saashub.com | https://www.saashub.com/submit | 403 bot-wall | Free | B | R | P1 |
| 16 | Indie Hackers (Products) | indiehackers.com | https://www.indiehackers.com/products/new | 403 bot-wall | Free | B | B + build-in-public post | P1 |
| 17 | Peerlist Launchpad | peerlist.io | https://peerlist.io/launchpad | 403 bot-wall | Free (weekly launch) | B | B | P2 |
| 18 | Uneed | uneed.best | https://www.uneed.best/submit-a-tool | 200 | Free queue; paid "skip the line". A free launch must hit an upvote threshold to keep its dofollow link | B/C | B | P1 |
| 19 | Fazier | fazier.com | https://fazier.com/submit | 200 | Free queue / paid featured | C | B | P1 |
| 20 | AIPure | aipure.ai | https://aipure.ai/submit | 200 | Free/paid options on page | B | A | P1 |

### Tier 2: AI-specific directories

| # | Directory | Site | Submission URL | Verified | Cost to list | Traffic | Copy | Priority |
|---|---|---|---|---|---|---|---|---|
| 21 | Aixploria | aixploria.com | https://www.aixploria.com/en/submit-ai-tool-or-feature-company/ | 200 | Free and paid tiers on page | B | A | P1 |
| 22 | Insidr.ai | insidr.ai | https://www.insidr.ai/submit-tools/ | 200 | Check on page | C | A | P2 |
| 23 | AI Tools Directory | aitoolsdirectory.com | https://aitoolsdirectory.com/submit-tool | 200 | Free, manual review | C | A | P1 |
| 24 | AI Valley | aivalley.ai | https://aivalley.ai/submit-tool/ | 200 | Free (dofollow per witscode, Oct 2026) | C | A | P1 |
| 25 | AICyclopedia | aicyclopedia.com | https://aicyclopedia.com/submit-your-ai-tool/ (linked from homepage) | 403 to script; homepage 200 | Free | C | A | P2 |
| 26 | Dang.ai | dang.ai | https://www.dang.ai/submit (redirects to /pricing; free route reported via dashboard after login) | 200 | Free via dashboard / paid featured | C | A | P2 |
| 27 | aitools.fyi | aitools.fyi | https://aitools.fyi/submit (opens a Tally form) | 200 | Check form | C | A | P2 |
| 28 | Toolpilot | toolpilot.ai | https://www.toolpilot.ai/pages/submit-your-ai-tool | 200 | Check on page | C | A | P2 |
| 29 | FindMyAITool | findmyaitool.com | https://findmyaitool.com/submit-tool | 200 | Check on page | C | A | P2 |
| 30 | AI Agents Directory | aiagentsdirectory.com | https://aiagentsdirectory.com/submit-agent | 200 | Free/paid | C | A (category: Marketing agents) | P1 |
| 31 | AI Agent Store | aiagentstore.ai | https://aiagentstore.ai/ (submit from site nav) | 200 | Check on page | C | A | P2 |
| 32 | Navtools | navtools.ai | https://www.navtools.ai/submit | 200 | Check on page | C/D | A | P3 |
| 33 | Twelve Tools | twelve.tools | https://www.twelve.tools/submit | 200 | Free/paid (pricing page) | D | A | P3 |
| 34 | AI Tool Trek | aitooltrek.com | https://www.aitooltrek.com/submit | 200 | Check on page | D | A | P3 |
| 35 | ToolAI | toolai.io | https://www.toolai.io/submit/tool | 200 | Check on page | D | A | P3 |
| 36 | AI Wizard | aiwizard.ai | https://www.aiwizard.ai/submit-new | 200 (login) | Check on page | D | A | P3 |
| 37 | AIGCList | aigclist.com | https://aigclist.com/submit | 200 | Check on page | D | A | P3 |
| 38 | AI Tool Mall | aitoolmall.com | https://www.aitoolmall.com/submit/ | 200 | Check on page | D | A | P3 |
| 39 | All AI Tool | allaitool.ai | https://www.allaitool.ai/submit-tool | 200 | Check on page | D | A | P3 |
| 40 | ToolsFine | toolsfine.com | https://www.toolsfine.com/submit-ai-tools-or-product | 200 | Check on page | D | A | P3 |
| 41 | aitools.inc | aitools.inc | https://aitools.inc/submit | 200 | Check on page | C/D | A | P3 |
| 42 | Altern Buildboard | altern.ai | https://altern.ai/buildboard | 200 | Free submit; paid placement | D | B | P3 |
| 43 | AI Depot | aidepot.co | https://aidepot.co/ (Submit Tool leads to checkout; free waitlist reported) | 200 | Freemium | D | A | P3 |
| 44 | Vantaige | vantaige.io | https://vantaige.io/list-your-ai-tool | 200 | Free (self-reported) | D | A | P3 |
| 45 | 10words | 10words.io | https://app.10words.io/submit (redirects to register) | 200 (login) | Check on page | D | Tagline only | P3 |
| 46 | Foundr.ai | foundr.ai | https://foundr.ai/submit | 200 (login) | Check on page | D | A | P3 |
| 47 | AIforThat.co (lookalike of TAAFT, separate site) | aiforthat.co | https://www.aiforthat.co/submit | 200 | Check on page | D | A | P3 |
| 48 | Owwly | owwly.com | https://owwly.com/submit | 200 | Not stated | D | A | P3 |
| 49 | Startup Dope | startupdope.com | https://startupdope.com/ (submit from nav) | 200 | Free standard (7–14 days) / paid priority | D | B | P3 |
| 50 | Toolfame | toolfame.com | https://toolfame.com/ (submit from nav) | 200 | Check on page | D | A | P3 |

### Tier 3: startup and indie launch boards

| # | Directory | Site | Submission URL | Verified | Cost to list | Traffic | Copy | Priority |
|---|---|---|---|---|---|---|---|---|
| 51 | Microlaunch | microlaunch.net | https://microlaunch.net/submit (redirects to premium pricing; free option listed there) | 200 | Free / premium | C | B | P2 |
| 52 | TinyLaunch | tinylaunch.com | https://www.tinylaunch.com/submit | 200 (login) | Free / paid | C | B | P2 |
| 53 | Open Launch | open-launch.com | https://open-launch.com/projects/submit | 200 (login) | Free / paid | C/D | B | P2 |
| 54 | Launching Next | launchingnext.com | https://www.launchingnext.com/submit/ | 200 | Free / paid expedite | C | B | P2 |
| 55 | LaunchIgniter | launchigniter.com | https://launchigniter.com/submit | 200 | Free / paid | D | B | P3 |
| 56 | StartupBase | startupbase.io | https://startupbase.io/launch | 200 | Free | D | B | P3 |
| 57 | Startup Stash | startupstash.com | https://startupstash.com/add-listing/ | 200 | Free / paid | C | A | P2 |
| 58 | Awesome Indie | awesomeindie.com | https://awesomeindie.com/submit | 200 | Free / paid | D | B | P3 |
| 59 | Startup Spotlight | startupspotlight.co | https://www.startupspotlight.co/submit | 200 | Check on page | D | B | P3 |
| 60 | Nick Launches | nicklaunches.com | https://nicklaunches.com/submit/ | 200 | Freemium (free permanent listing + followed link per Vantaige) | D | B | P3 |
| 61 | SaaSFame | saasfame.com | https://saasfame.com/submit | 200 (login) | Free / paid | D | B | P3 |
| 62 | PeerPush | peerpush.com | https://peerpush.com/ (peerpush.net redirects here) | 200 | Check on page | D | B | P3 |
| 63 | BetaPage | betapage.co | https://www.betapage.co/submit-startup | 403 bot-wall | Free / paid | C/D | B | P3 |
| 64 | F6S | f6s.com | https://www.f6s.com/ (create a company profile) | 200 | Free | C | A | P3 |
| 65 | Wellfound | wellfound.com | https://wellfound.com/ (company profile) | 200 | Free | B | A | P3 |
| 66 | WebCatalog | webcatalog.io | https://webcatalog.io/en | 200 | Free (app request) | C | A | P3 |

### Tier 4: software review and comparison sites (SEO and "alternatives" pages)

| # | Directory | Site | Submission URL | Verified | Cost to list | Traffic | Copy | Priority |
|---|---|---|---|---|---|---|---|---|
| 67 | SaaSworthy | saasworthy.com | https://www.saasworthy.com/list-your-product | 403 bot-wall | Free basic | B/C | R | P2 |
| 68 | SoftwareSuggest | softwaresuggest.com | https://www.softwaresuggest.com/vendors | 200 | Free basic | B/C | R | P3 |
| 69 | TrustRadius | trustradius.com | https://www.trustradius.com/vendors | 403 bot-wall | Free basic | B | R | P3 |
| 70 | SaaS Genius | saasgenius.com | https://www.saasgenius.com/get-listed/ | 200 | Check on page | D | R | P3 |

### Tier 5: MCP directories (an under-used channel; Fuzzbucket has a live remote MCP server)

| # | Directory | Site | Submission URL | Verified | Cost to list | Traffic | Copy | Priority |
|---|---|---|---|---|---|---|---|---|
| 71 | MCP.so | mcp.so | https://mcp.so/submit (to ?type=server) | 200 | Free | C | M | P1 |
| 72 | Glama MCP | glama.ai | https://glama.ai/mcp/servers ("Add server") | 200 | Free | C | M | P1 |
| 73 | Smithery | smithery.ai | https://smithery.ai/new (sign-in) | 200 (login) | Free | C | M | P1 |
| 74 | PulseMCP | pulsemcp.com | https://www.pulsemcp.com/submit | 403 bot-wall | Free | C | M | P1 |
| 75 | MCP Servers (mcpservers.org) | mcpservers.org | https://mcpservers.org/submit | 200 | Free / paid featured | C/D | M | P2 |

**Meta-resource (not a listing):** https://launchdirectories.com/ (200) keeps an up-to-date index of launch boards. Use it to backfill if an entry above dies.

---

## Recommended sequence

1. **This week (P1, about 3 hours):**
   - Free listings: #5, 12, 14, 15, 16, 18, 19, 20, 21, 23, 24, 30, 71–74.
   - Paid: #2 TAAFT at $49, and #4 Toolify after confirming the price.
   - Submit early because review queues run 1–6 weeks. Set each listing's go-live to the Product Hunt date where the form allows it.
2. **Launch week (P2):**
   - Paid boosts only if PH day-1 results justify them: TAAFT upgrade, Futurepedia enhanced listing, Uneed skip-the-line.
   - Review-site profiles (G2/Capterra) once there are 10+ paying users to ask for reviews.
3. **Backlog (P3):**
   - Batch the long tail in one sitting with copy set A.
   - Track it in a sheet: directory, date submitted, status, live URL, dofollow (y/n), signups by UTM.

## Dead, parked or changed routes (do not use)

| Route | Result on 2026-10-21 |
|---|---|
| futurepedia.io/submit-tool | 404 (no public new-tool form) |
| aitoolhunt.com | 521, offline |
| allthingsai.com | DNS failure |
| aiscout.net | 522 |
| aimojo.io/submit-ai-tool | 526 |
| saasaitools.com | 526 |
| aitoolsclub.com/submit-your-ai-tool | 404 |
| aitoptools.com/submit | 404 |
| toolsforhumans.ai/submit | 404 |
| topapps.ai/submit | 404 |
| superbcrew.com/submit | 404 (advertise page only) |
| ai-finder.net | domain for sale |
| aitoolkit.org, marketingtools.ai | parked or for sale |
| gpt3demo.com/submit, ainavigator.ai, aitools.neilpatel.com, findaitools.me, launched.io, erlibird.com | no response |
| opentools.ai/launch | 404 (look for a new route on the site) |
| getapp.com/vendors | 404; use the Capterra vendor portal, which covers GetApp |

Sources for cost and traffic notes: the TAAFT /launch page (fetched 2026-10-21); [witscode AI directory audit (Oct 2026)](https://witscode.com/resources/saas-launch-playbook/ai-tool-directories); [Vantaige 2026 directory guide](https://vantaige.io/blog/where-to-list-your-ai-tool-2026); [SaaSCity Futurepedia review](https://saascity.io/directories/futurepedia).
