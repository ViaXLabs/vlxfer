Using vs code on Mac… need a way to develop a dev image that users load up for their own environment with all the local apps and specific versions…

We need to have a process that we can have this built in a harness pipeline and published to nexus…. We have templates for all that 

But need a process for devs and testers to load this up and use it

We will need to incorporate our AWS jump box and a python app that loads up an awshelper app into thus dev image….

But thus will be a shift in process so doc it and explain the goodness of this 

Mostly for Mac users … we may have a win user a few

Outline is up; now drafting the summary.

Standardized Dev Container Images: Process & Rationale
Claude Docs·Only you 

Summary is in; now the benefits section.

Your dev image process doc, Standardized Dev Container Images: Process & Rationale, is ready. Read it over, edit inline, or comment.

I assumed some details, such as the Nexus and AWS access methods, so check the open questions at the end.

Well, it's sort of annoying to have a separate doc to look at, even though I'm trying to read this in chat on my phone. Okay, so, I think we have licenses for Docker Desktop, and that gets installed, you know, with, using that whatever Mac controlled installer. Oof. Anyway, so let's maybe hold off on the Kalima. But anyways, let's sort it out all here and give good doc and stuff.

Updated. Docker Desktop now comes through your managed Mac installer, and Colima is out.

Here’s the whole process, so you don’t have to open the doc:

What we’re doing: one versioned dev image with pinned tools plus the awshelper app. Everyone runs the same environment, so “works on my machine” mostly goes away.

How it flows:

A PR changes the Dockerfile or tool list.
Harness builds, scans, and publishes the tagged image to Nexus. awshelper comes from your Nexus PyPI repo.
Devs and testers pull that tag and open it in VS Code.

Dev/tester setup (Mac, about 15 min, once):

IT’s installer provides Docker Desktop. Install VS Code if needed.
Add the Dev Containers extension.
Run docker login to Nexus.
Clone the repo, which holds a .devcontainer/devcontainer.json pinned to an image tag like 1.4.0.
Run Dev Containers: Reopen in Container.

After that, daily use is just opening the folder. To update, bump the tag and rebuild.

AWS: awshelper is baked into the image and runs a check at start. Credentials are mounted from ~/.aws read-only and never baked in. Users need VPN to reach the jump box.

Why it’s worth the shift: faster onboarding, reproducible bugs (testers record the tag), and one place to patch, scan, and roll back.

Windows: same image via Docker Desktop with WSL 2, best effort.

Two things I still need from you:

How do people reach the jump box today (SSM or SSH)?
How do they authenticate to AWS (SSO, keys, or assumed roles)?

If you’d rather skip the doc entirely from now on, I’ll keep everything here in chat.

Yeah, so we use JAMF, JMF on Macs and stuff, and I'm pretty sure Docker Desktop, we can get licenses for all that stuff. So yeah, we're just trying to make this an easy setup for people. You know, maybe our platform team gets, you know, has a base dev image, and that has the connections to Jumpbox and all the stuff and all the tools people would need and stuff like that. And let's just, you know, see if, you know, we can deliver an image and figure out how it works and post it and see if it would be useful and such. Make sure we document how to use it and, you know, including like, hey, what you do in the morning to make sure it gets loaded up. And then, you know, if you're going to have like user repo with it, right? How do our local repos, so everyone has all their, you know, repos loaded up in their /home/repo folder, and how does that work with the dev image? Maybe we have we have everyone load up all the repos to /repos, and then, you know, that is on their Macs local, and then maybe that drive is, you know, available in the dev image. I'm not exactly sure the best way to handle that, but we will have to, you know, get repo code, and so I don't know how this all works together and such.

Your plan works. I added the morning routine and the repo setup to the doc, and here’s the short version.

Repos: keep them on the Mac in ~/repos and mount that folder into the container at /repos. Repos survive container rebuilds, show up in Finder, and use your normal Git and SSH sign-in. VS Code passes those credentials into the container, so no keys go in the image. The repo’s devcontainer.json mounts the whole folder but opens one repo, so multi-repo work can still reach sibling folders. A few cautions:

Don’t clone inside the container, or the work is lost if the container is removed.
Keep node_modules and Python virtual environments in Docker volumes, because Mac bind mounts are slow for those.
Use /repos in the container rather than /home/repo.

Morning routine:

Connect to VPN and check that Docker Desktop is running.
Run git pull in the repos you’ll use.
Open VS Code, open the repo from ~/repos, and choose Reopen in Container.
Check the awshelper output. If it flags a problem, fix VPN or AWS login.
Only when release notes announce a new image, bump the tag and rebuild.

Rollout: the platform team publishes a base image with the tools and awshelper. Jamf pushes Docker Desktop and VS Code to the pilot Macs. Pilot with one dev team and one tester for two weeks, then decide if it’s worth expanding. I updated the doc to say Jamf instead of the generic installer. Docker licensing still needs confirming for all Mac users.

I still need to know how you reach the jump box today (SSM or SSH) and how you sign in to AWS (SSO, keys, or assumed roles). Those two answers decide how awshelper connects.