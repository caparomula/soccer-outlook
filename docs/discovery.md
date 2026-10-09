# Helping people find Soccer Outlook

Soccer Outlook can be found through ordinary web search. Its public schedules are readable without JavaScript, allow indexing, and link to one another. The site publishes a [sitemap](https://caparomula.github.io/soccer-outlook/sitemap.xml) that lists these pages:

- [Soccer Outlook](https://caparomula.github.io/soccer-outlook/) — the main schedule with personal filters and recommendations.
- [Premier League TV schedule](https://caparomula.github.io/soccer-outlook/premier-league/) — Premier League fixtures and US viewing options.
- [MLS TV schedule](https://caparomula.github.io/soccer-outlook/mls/) — MLS fixtures and US viewing options.
- [Soccer on Paramount+](https://caparomula.github.io/soccer-outlook/paramount-plus/) — matches available through Paramount+.

The three focused pages show the current four-day schedule in Eastern time, without applying a visitor's saved filters. Each schedule day starts at 4 a.m.; kickoff labels give the actual calendar date and Eastern time. These are snapshots from the latest build, including results and matches in progress at that update, rather than continuously updating live scores. Cancelled and postponed matches are omitted. They link to the main app for personal filters, local times and live updates.

The pages remain useful at the same URLs as fixtures change; an empty schedule explains when no matches are available in that window. Established usual broadcast rights are distinguished from confirmed match listings, and viewers still need the appropriate subscriptions or channel access.

No advertising, social-media account or visitor analytics is needed. Search engines decide whether and where a page appears; a sitemap or submission does not guarantee a listing or a ranking.

## Register with Google

The site owner completes this once using their own Google account. No account password or search-console access needs to be shared with this project.

1. Open [Google Search Console](https://search.google.com/search-console/) and add a **URL prefix** property for `https://caparomula.github.io/soccer-outlook/`. Do not choose a Domain property for `github.io`: that would require control of GitHub's DNS.
2. Choose the **HTML tag** verification method. Google supplies a tag such as `<meta name="google-site-verification" content="YOUR_TOKEN">`. Copy only the value inside `content`, without quotes or the rest of the tag.
3. In the GitHub repository, open **Settings → Secrets and variables → Actions → Variables**. Create the repository variable `GOOGLE_SITE_VERIFICATION` with that token as its value. This is a public verification token, not an API key or a password; it will appear in the page source.
4. Open **Actions → Refresh outlook → Run workflow**, choose `main`, and set **storylines** to `keep`. Wait for the refresh and the subsequent GitHub Pages deployment to succeed. This publishes the tag; saving the variable alone does not change the site.
5. Return to Search Console and click **Verify**. Keep the repository variable afterwards so future builds continue to prove ownership.
6. In **Sitemaps**, submit `https://caparomula.github.io/soccer-outlook/sitemap.xml`. If the form already supplies the property prefix, enter only `sitemap.xml`.
7. Use **URL inspection** for the homepage and, if needed, the three focused pages, then choose **Request indexing**. Repeated requests do not make crawling faster.

After Google has had time to crawl, **Page indexing** explains exclusions and errors, while **Performance** shows search terms and visits from Google. These reports work without adding visitor-tracking code to the app. See Google's [ownership-verification instructions](https://support.google.com/webmasters/answer/9008080) and [recrawl guidance](https://developers.google.com/search/docs/crawling-indexing/ask-google-to-recrawl).

## Register with Bing

Open [Bing Webmaster Tools](https://www.bing.com/webmasters/) with your own account. Either import the verified property from Google Search Console, or add `https://caparomula.github.io/soccer-outlook/` manually.

For manual verification, choose Bing's HTML meta-tag method. Copy just the `content` value from its `msvalidate.01` tag into the repository Actions variable `BING_SITE_VERIFICATION`. Run **Refresh outlook** with `storylines` set to `keep`, wait for Pages to deploy, and finish verification in Bing. Leave the variable in place after verification. You do not need this separate token if importing from Google completes verification.

Submit the same full sitemap URL in Bing and check its indexing reports. Google verification, Bing verification and IndexNow are separate mechanisms; configuring one does not complete the others.

## Notify search engines when the schedule changes

[IndexNow](https://www.indexnow.org/) tells participating search engines, including Bing, that published pages have changed. Google is not an IndexNow participant. Notifications help with discovery and freshness; they do not guarantee indexing or search placement.

The site uses the stable public ownership token in [`web/indexnow-key.txt`](../web/indexnow-key.txt). A full build copies it into a file named after the token beside the homepage. Notifications include that file's URL, allowing verification under `/soccer-outlook/` without control of the domain root. Treat the key as a public token, not a secret or an AI provider credential. This is already configured for Soccer Outlook; no account registration or repository variable is required for IndexNow.

For a fork, replace `web/indexnow-key.txt` with a new random token and keep it stable across builds. Generate one with:

```sh
python3 -c 'import secrets; print(secrets.token_hex(16))'
```

The [Notify search engines](../.github/workflows/discovery.yml) workflow sends notifications only after a successful production Pages deployment. Its Actions log records the outcome; a notification failure does not take down the schedule.

## Earn useful links

A permanent link from a relevant website can help readers find the schedule directly. A supporters' club's matchday guide, a soccer resource directory, or an independent guide to watching soccer in the US may be a good fit. Check that the resource is maintained and actually serves this audience before contacting its editor. A handful of useful listings is enough to start; avoid purchased links and bulk messages.

Here is a short draft the owner can adapt and send:

> Hello — I maintain Soccer Outlook, a free schedule of soccer on US TV and streaming services: https://caparomula.github.io/soccer-outlook/. It shows today and the next three days, with filters for the services and leagues a viewer follows. If it would help readers of your matchday/resources page, would you consider including a link? It needs no account and has no advertising. It lists viewing options; viewers still need the relevant subscriptions or channel access.

For a Premier League or MLS resource, link directly to that league's page. Describe it accurately as a schedule, not a place to watch free streams.

On the GitHub repository homepage, use the gear beside **About** to set the website to `https://caparomula.github.io/soccer-outlook/`. Keep the existing `soccer` and `soccer-matches` topics and add `tv-schedule`, `streaming` and `sports`. This helps people browsing GitHub recognize the app and find the published schedule. Editing these fields requires a repository administrator or another account with the appropriate access.

## Maintain the public pages

Keep these pages focused on useful, current schedules. Do not generate a page for every possible filter combination: that creates many repetitive or empty results. Add another league or service page when it offers a useful distinct schedule, and give it a stable URL, accurate title, ordinary navigation links and a sitemap entry.

For the generator, deployment configuration and local build commands, see [Development and maintenance](development.md). Return to the [project overview](../README.md).
