Okay, so I'm working in the harness pipeline and have a lot of good things going for it, and I need to be able to read in from Confluence, and I have all the things I need to, keys and tokens I need to read in from Confluence, so read a Confluence page. However, the page is built with a bunch of macros, and I want to take the table and make it into some data. Okay, my other chatbot says, yeah, we can do this, but how can I get the current Confluence page to export the Confluence page as a PDF? Is there some kind of endpoint that can be done or extract? I don't know how to get that, you know, mechanism to make it a page, a PDF export on the fly. So need some help with that.

Searched the web
There is a way to get a PDF, but I’d steer you away from it for this job.

The PDF route (unofficial)

Atlassian has no official REST endpoint for PDF export. The documented workaround is the FlyingPDF export action, which you call with the page ID: 
atlassian

GET <BASE_URL>/spaces/flyingpdf/pdfpageexport.action?pageId=<PAGE_ID>
On Data Center, that call responds with a 302 redirect, and the Location header holds the URL of the generated PDF. You then make a second authenticated request to that URL to download the file. So in a script you’d disable auto-redirects, read the Location header, and fetch it. 
atlassian

Cloud is less clean. Developers report that the same URL returns an HTML page with a download link rather than a straight redirect, so you may have to scrape the link out of the response. Even then, you’d still have to pull a table out of a PDF, which is lossy and brittle. 
atlassian

What I’d do instead: pull the rendered HTML and parse the table

The REST API can return the page body with macros already executed. Ask for body.export_view. One person used it to get the executed output of a Page Properties Summary macro as HTML. Then pandas.read_html turns the tables straight into DataFrames: 
atlassian

python
import os, requests, pandas as pd
from io import StringIO

base = os.environ["CONFLUENCE_BASE"]  # Cloud: https://yourco.atlassian.net/wiki
auth = (os.environ["CONFLUENCE_USER"], os.environ["CONFLUENCE_TOKEN"])

r = requests.get(f"{base}/rest/api/content/{PAGE_ID}",
                 params={"expand": "body.export_view"}, auth=auth)
r.raise_for_status()
html = r.json()["body"]["export_view"]["value"]

df = pd.read_html(StringIO(html))[0]   # pick the right table by index
df.to_json("table.json", orient="records")
In a Harness Run step, map your Confluence token from a Harness secret into the environment variable. On Data Center with a personal access token, send it as a Bearer header instead of basic auth.

One caveat

Not every macro renders fully through the API. One user found that a Pivot Table macro came back only partly calculated, and dynamic macros like Jira reports tend to be the worst offenders. If your table comes from a macro like that, the PDF export may be more faithful, since it renders the page fully. 
atlassian
atlassian

Is this Confluence Cloud or Data Center, and which macro is generating the table? That decides whether export_view is enough or whether you need the PDF fallback.

I mean, the real crux is not necessarily to make the PDF, but to extract the data that's in a table from the Confluence page. Now, this Confluence page is built up with macros, so, you know, behind the scenes, it's not like that. So I don't know if there's like an HTML of the page that's better or not. So keep that in mind.

Deciding whether to keep code snippets short or build a full script.
Yes, Confluence will give you the page body in a few different forms, and which one works depends on how the table gets onto the page.

The three forms

body.storage is the raw source: XHTML with the macros left as tags and parameters. Nothing is executed. If the table is typed directly into the page, even inside macros like Expand, Section or Panel, the real <table> is right there. If a macro generates the table, storage only has the macro’s settings. One person who pulled a page with an SQL macro got back just the query text, not the results. 
atlassian
body.export_view is the rendered version, with macros executed server-side. It’s usually your best bet for macro-built tables. It’s what resolved a Page Properties Summary macro for someone. 
atlassian
body.view is similar but works less well for pre-rendered macros than export_view does.
Find out which one has your table

Fetch storage and export_view and count the tables in each:

python
for fmt in ("storage", "export_view"):
    r = requests.get(f"{base}/rest/api/content/{PAGE_ID}",
                     params={"expand": f"body.{fmt}"}, auth=auth)
    html = r.json()["body"][fmt]["value"]
    print(fmt, html.count("<table"), "tables")
If both have it, use export_view. Cells with status lozenges, user mentions or Jira links show up as raw macro XML in storage, whereas export_view gives you the display text.
If only export_view has it, a macro is generating the table, and export_view is the right source.
If neither has it, the macro renders in the browser with JavaScript. Then go to the underlying data directly (for a Jira macro, query Jira’s API), or fall back to the PDF export. Dynamic macros like Jira reports are the ones that tend to render poorly through the API. 
atlassian
Pick the right table

Macro-heavy pages usually have several tables, so grab yours by a distinctive column header rather than by position. Rendered Confluence tables typically carry the class confluenceTable:

python
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, "html.parser")
tables = soup.select("table.confluenceTable")
target = next(t for t in tables if "Your Column Header" in t.get_text())
df = pd.read_html(StringIO(str(target)))[0]
That gives you a DataFrame you can dump to JSON or CSV for the next pipeline step.

What generates the table on your page: a Page Properties Report, a Jira issues macro, a spreadsheet or CSV viewer, something custom? That tells me whether export_view will cover you or whether you should skip Confluence and hit the data source directly.

Well, I wanted to mention the macro that this table is held in is a coniform macro, and I don't know if it's worth using trying to get to that coniforms, macro or whatever. It looks like it gets put into a coniforms table view macro, and I'm not exactly sure about the source. I think it just gets pushed there from like some kind of wiki thing. I'm not exactly, maybe it's a coniform from a different page. I don't exactly know, but that's what the plugin is. So if that helps, then great. Combine all the answers that you've just put together into a bunch of different things to try and figure out how we can do this with scripts and my harness pipeline to try this out. So let's, and if we script it, it all needs to be kind of in Python. That's the best way to do stuff. So let's try it all out.

Ran 2 commands, searched the web, and 2 more tools, 1 failed
I wrote the script, and my local test against a mock server hung, probably because the background server held the shell open. I’m rerunning it with timeouts.

Ran 3 commands, shared files
I built confluence_table_extract.py, one Python script with four strategies, so you can try each in your pipeline. I tested it against a mock Confluence server I wrote, not your real instance, so expect to tweak it once it sees your actual page.

The ConfiForms plugin changes the plan. It has its own data export, which beats scraping HTML or a PDF. You can switch on export in the TableView macro, and that adds a CSV download link. ConfiForms exports as CSV, XLS, JSON or XML. CSV and XLS are meant for end users, while JSON and XML give internal values like dropdown IDs and epoch timestamps. So the ConfiForms route is the one to try first, and PDF is the last resort. 
lithium
vertuna

Order to try

probe shows what’s on the page. It prints the macro names, each ConfiForms macro with its parameters, the table count in each body format, and any export links it finds. That should also settle your hunch about the data living on another page, because the macro parameters should show the form name and source page ID. It saves the raw HTML so you can look at it.
confiforms downloads the export CSV or JSON and converts it to records. It auto-discovers the link from the rendered page, or you can pass --url. If no link shows up, enable export on the TableView macro. Or click the CSV link in your browser with DevTools open on the Network tab and copy that URL.
table parses a table out of export_view. It works if the ConfiForms table is rendered server-side. Use --contains "Some Column Header" to pick the right table.
pdf uses the FlyingPDF export, and --tables extracts tables with pdfplumber.
Caveats

The ConfiForms docs describe the Server/Data Center app, and say the export approach often works on Cloud too. If you’re on Cloud, the export may need a browser session, and the script will tell you if it gets a login page back instead of data. 
vertuna
Non-admin users can’t export unless exports are explicitly allowed in the form definition, so check that if your service account gets blocked. 
vertuna
ConfiForms exports can be slow on big forms, so the script waits up to 10 minutes.
Harness step (adjust to your connector and repo layout)

yaml
- step:
    type: Run
    name: Extract Confluence table
    identifier: extract_confluence_table
    spec:
      connectorRef: <+input>
      image: python:3.12-slim
      shell: Sh
      envVariables:
        CONFLUENCE_BASE: https://yourco.atlassian.net/wiki
        CONFLUENCE_USER: <+secrets.getValue("confluence_user")>
        CONFLUENCE_TOKEN: <+secrets.getValue("confluence_token")>
        PAGE_ID: "123456789"
      command: |
        pip install -q requests beautifulsoup4
        python confluence_table_extract.py --out out probe
        python confluence_table_extract.py --out out confiforms
For a first run, only the probe line matters. Run it, then paste me the output with the token and any sensitive values redacted. I’ll tell you which strategy to lock in and help you wire the result into the next pipeline step.


Confluence table extract
PY 
