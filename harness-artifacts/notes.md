Tell me all about harness artifacts…. This is something new I think

“Harness artifacts” doesn’t seem to be one settled term yet. My search turned up two different uses, and you may mean a third.

1. Harness (the CI/CD company): Artifact Registry
Harness Artifact Registry is a universal hub for artifacts, regardless of type, and centralizes artifact management in one secure location. It stores build outputs, container images (Docker, Helm) and package dependencies (Maven, npm, Go, Python). It plugs into their CD pipelines. This is the older, conventional meaning. Their newer pitch is one registry for software, AI, and ML artifacts, with agents managing artifact lifecycle and risk. 
harness
harness

2. Atlan: “harness” as an artifact for AI agents
This one is newer and probably closer to what you mean. Atlan’s CLI docs describe harness artifacts as records of where agents run. You can list, inspect, create, version and archive them, for example by pushing a YAML or JSON definition. Each push appends a new version, and deleting is a soft delete that keeps the versions. In effect, the agent’s runtime setup becomes a versioned, shareable object, like code. 
atlan

3. The general “agent harness” idea (my own background knowledge)
In AI engineering, a harness is the scaffolding around a model: its tools, prompts, memory and loop. The artifacts it produces or depends on include progress logs, feature checklists, init scripts and test results. They carry state between sessions for long-running agents. I haven’t confirmed this against a specific source, so treat it as context rather than a definition.

Where did you come across the term? That would tell me which of these it is.

This is harness pipelines… number 1… how can I use artifacts in pipe stages and between pipes… how and where they are saved

Short version: Harness doesn’t give you a built-in “pipeline artifact store” like GitHub’s upload/download-artifact. Files are shared within a stage through a workspace volume. Between stages and pipelines, you push them to external storage or to Harness Artifact Registry (HAR) and pull them back.

Within a stage (steps)
Each stage gets a temporary workspace volume, with /harness as the default shared working directory. Any step in the stage can create, read, update or delete files there. The workspace is destroyed when the stage ends. To share paths outside /harness, such as /root/.m2, add Shared Paths in the Build stage settings. 
harness

Between stages
You upload artifacts in one stage and pull them into another stage, for example with a Run step. Options: 
harness

Upload Artifacts to S3 or GCS, then download with a Run step (aws or gsutil CLI) or the plugins/gcs image in the later stage. If you want a compressed file, compress it with a Run step first. The S3 step is only available inside a containerized step group. 
harness
harness
Cache Intelligence is meant for dependencies and caches. You can’t share credentials or other text secrets this way. 
harness
Harness Artifact Registry is the better fit for images, packages and binaries. You push in one stage and reference it in a later stage.

Harness has no native workspace handoff between stages. A user thread notes sharing files between CI and CD stages isn’t possible because of how CI and CD operate with the Harness Delegate. Many people are asking for this, so check whether it has changed. 
harness

Between pipelines
Same idea: pipeline A pushes to S3, GCS, or HAR, and pipeline B pulls from there. The two common ways to hand over the location are a pipeline input (or trigger) that carries the artifact path/version, or a fixed naming convention. In CD, you can set HAR as the artifact source on a service, and the pipeline automatically fetches the artifact during execution. 
harness

Where things are saved

Workspace files: temporary, on the stage’s volume, gone when the stage ends.
S3/GCS uploads: in your own bucket and path.
HAR: in the registry, in your Harness account.
Artifacts tab on the Build details page: this stores only the URL, not the file. You add a Plugin step after the upload using plugins/artifact-metadata-publisher, with the artifact URL as file_urls. Then you select the step name on the Artifacts tab to see its links. The upload location must be publicly available to open it directly from the tab, otherwise you may need to log in. 
harness
harness

If you tell me what you’re moving (a jar, a Docker image, test reports) and which storage you have, I can sketch the YAML for the upload and download steps.

Building the example pipelines that use both templates, then I’ll validate the YAML.

I’ve built both templates plus an example file showing a producer pipeline and a different pipeline that downloads its output. I assumed classic Harness YAML, not the newer Harness 3.0 syntax, and I haven’t run any of it in a live Harness account. The files parse as valid YAML, but treat the field shapes as drafts until you’ve saved and run them once.

Where they’re stored: Harness holds the template, not your repo. It sits at the scope you save it to: Account Settings → Templates, or the org or project equivalent. You can instead keep it in a Git repo (a “remote” template), but you must create the repo and branch yourself. To install, create a Step template with version label 1.0.0 and paste the YAML in Template Studio. Terraform’s harness_platform_template resource also works.

How other pipelines get it:

Referencing: a step points at the template with templateRef: account.S3_Upload_Artifact plus a versionLabel, then fills in the inputs. In the UI that’s Add Step → Use Template. example-pipelines.yaml shows this.
Who can see it: an account-level template is visible to every org and project in the account. Callers need the template Access permission.
Updates: Always use stable pushes new versions to every pipeline. Pinning 1.0.0 doesn’t.

Before first use:

Placeholders: replace the AWS connector, bucket, and secret identifiers in the examples.
S3 key: the upload step fixes it as <project id>/<pipeline id>/<run number>. That uses identifiers rather than names, so it differs slightly from the doc, which says pipeline name. Say the word and I’ll align the doc.
Download: check the downloaded folder layout and single-file behaviour on a first run.

Files are in S3_Upload_Artifact.template.yaml, S3_Download_Artifact.template.yaml, and example-pipelines.yaml.

S3 upload artifact.template
YAML 
S3 download artifact.template
YAML 
Example pipelines
YAML 

How does the structure look in s3….

Explaining the S3 key structure for pipeline run files.

With the templates as built, each upload lands under a key made of the project id, pipeline id and run number. Using the example pipelines, the bucket looks like this:

text
YOUR_ARTIFACT_BUCKET/
└── payments/                    project id
    ├── build_api/               pipeline id
    │   ├── 141/                 run number
    │   │   └── app.tar.gz
    │   └── 142/
    │       └── app.tar.gz
    └── build_web/
        └── 57/
            └── site.tar.gz

The full key for the latest run is payments/build_api/142/app.tar.gz. S3 has no real folders, only key prefixes, so the console just draws them as folders. Each run gets its own prefix, so runs never overwrite each other. The download template’s source points at one run’s folder, for example payments/build_api/142.

Harness’s own docs show the source path sometimes appearing inside the key. So if your sourcePath is build/output/app.jar, the key may come out as .../142/build/output/app.jar. Keeping the file at the workspace root, as the example does, avoids that. Otherwise set the PLUGIN_STRIP_PREFIX stage variable. Check the real key in S3 after your first run before anything depends on the layout.