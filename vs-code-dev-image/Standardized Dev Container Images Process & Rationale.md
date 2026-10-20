# Standardized Dev Container Images: Process & Rationale

Oct 17, 2026 · @Orin Bishop

## Summary

We will stop building dev environments by hand and ship one versioned dev container image instead. Harness builds it, Nexus stores it, and every developer and tester opens it in VS Code with one command.

The image carries the pinned versions of all local apps and tools, plus the Python awshelper app that connects to our AWS jump box. Everyone on the team runs the same environment, on Mac first, with Windows supported for the few users who need it.

This changes how people set up and update their machines, so this document covers the why, the how, and the rollout.

## Why this is better

A shared image removes the "works on my machine" class of problems, because everyone runs the same OS, tools and versions.

| Who | What improves |
| --- | --- |
| Developers | New machine to productive in minutes, not days. No manual installs, no version drift between laptops. |
| Testers | Test against the exact environment developers built in. Bug reports reproduce on the first try. |
| Support and IT | One image to patch and audit instead of many laptops. Rolling back a bad update is a tag change. |
| Security | Tool versions and dependencies are pinned, scanned in the pipeline, and traceable to a build. |
| AWS access | The jump box connection and awshelper ship preconfigured, so access is consistent and documented. |

The image is also disposable. If an environment gets into a bad state, rebuild the container and the laptop itself stays clean.

## Architecture

Harness builds the image from the repo, Nexus stores it, and each Mac pulls and runs the same tagged version.

&#91;embedded content: dev image architecture · 6 components\]

The top row is the build side, owned by the platform team. The bottom row is what developers and testers do: pull the tag, reopen in the container, and reach the jump box through awshelper.

## Developer and tester onboarding

Onboarding is a one-time setup of about 15 minutes, then a single action each day. Placeholders in angle brackets are filled in by our platform team.

### One-time setup on Mac

1. Docker Desktop is deployed to your Mac through Jamf. Install VS Code through Jamf too if it is not already there. Install VS Code if it is not already there.
2. In VS Code, install the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).
3. Log in to Nexus: `docker login <nexus-host>:<port>` using your SSO or Nexus token.
4. Clone your project repo. It contains a `.devcontainer/devcontainer.json` that points at the shared image.
5. In VS Code, run **Dev Containers: Reopen in Container**. The first pull takes a few minutes; later opens take seconds.

### What the repo file looks like

```json
{
  "name": "team-dev",
  "image": "<nexus-host>:<port>/dev-images/team-dev:1.4.0",
  "remoteUser": "dev",
  "mounts": [
    "source=${localEnv:HOME}/.aws,target=/home/dev/.aws,type=bind,readonly"
  ],
  "postStartCommand": "awshelper --check"
}
```

Pinning an explicit tag such as `1.4.0` keeps everyone on the same version until the team chooses to move. Avoid `latest` for shared work.

### Day to day

- Open the project folder in VS Code and accept the **Reopen in Container** prompt.
- To update, change the tag in `devcontainer.json`, commit, and run **Dev Containers: Rebuild Container**.
- Testers use the same file, pinned to the release candidate or release tag under test.

### Morning routine

1. Connect to VPN and make sure Docker Desktop is running (the whale icon in the menu bar).
2. Run `git pull` in the repos you will work on, from a Mac terminal or VS Code.
3. Open VS Code, open your repo from `~/repos`, and choose **Reopen in Container** (or pick it from the recent list).
4. Watch the terminal for the awshelper check. If it reports a problem, fix VPN or AWS login and rebuild.
5. Only if release notes announce a new image: change the tag in `devcontainer.json` and run **Rebuild Container**.

### Local repos and the container

Keep all repos on the Mac in `~/repos` and mount that folder into the container at `/repos`. Repos then survive container rebuilds, show up in Finder, and use the Mac's Git and SSH sign-in.

```json
{
  "workspaceMount": "source=${localEnv:HOME}/repos,target=/repos,type=bind,consistency=cached",
  "workspaceFolder": "/repos/<repo-name>"
}
```

This mounts the whole `~/repos` folder but opens VS Code in one repo, so multi-repo work can reach sibling folders. VS Code forwards your SSH agent and Git credentials into the container, so no keys go into the image.

- **Do not** clone inside the container's own file system unless you accept losing the work when the container is removed.
- Keep heavy generated folders (such as `node_modules` or Python virtual environments) in named Docker volumes to avoid slow Mac file access.
- Windows users keep repos inside WSL 2 and mount from there.

### Windows users

The same image works on Windows with Docker Desktop using the WSL 2 backend. Keep the repo inside the WSL file system for acceptable performance, and use the same Dev Containers steps. We support Mac first and handle Windows on a best-effort basis.

## AWS jump box and awshelper

The image installs awshelper at build time and checks it at container start, so every user reaches the jump box the same way. The exact connection method is an open item (see the last section); the design below works for SSM or SSH.

**Build time.** The Dockerfile installs Python at a pinned version, then installs awshelper from our Nexus PyPI repository at a pinned version. This keeps the app versioned and scanned like everything else in the image.

```dockerfile
FROM <nexus-host>:<port>/base/ubuntu-dev:22.04
RUN pip install --no-cache-dir \
    --index-url https://<nexus-host>/repository/pypi-group/simple \
    awshelper==<version>
```

**Start time.** `postStartCommand` runs `awshelper --check` to confirm credentials are present, the jump box is reachable, and the user's profile is valid. It prints a clear message if not.

**Credentials.** Do not bake secrets into the image. Users mount their local `~/.aws` folder read-only, or sign in with SSO from inside the container. The image holds tooling and configuration, never keys.

**Network.** Users must be on VPN or an approved network to reach the jump box. awshelper should fail with a plain-language hint when they are not.

**Ownership.** The awshelper repo has its own pipeline that publishes to Nexus. A new awshelper release is picked up by the next image build, so app fixes reach users through a normal image update.

## Versioning and release

Every image is immutable and tagged with a semantic version, so a tag always means the same bits.

| Tag | Meaning | Who uses it |
| --- | --- | --- |
| `1.4.0` | Released, immutable | Developers and testers (default) |
| `1.4` | Latest patch of a minor version | Teams that accept patch updates |
| `1.5.0-rc.1` | Release candidate | Testers validating a change |
| `latest` | Newest release | Quick experiments only, not shared work |

**Release flow.**

1. A change to the Dockerfile, tool list or awshelper version is made by pull request.
2. The Harness pipeline builds the image, runs smoke tests (tool versions, awshelper check), and scans it for vulnerabilities.
3. On merge, Harness publishes the tagged image to Nexus and posts release notes with the tool version changes.
4. Teams update the tag in their `devcontainer.json` when ready, and rebuild.

**Cadence.** A monthly release for routine updates, plus out-of-band patches for security fixes. Keep at least the last three minor versions in Nexus so teams can roll back.

## The process shift

The biggest change is that the environment becomes a product with an owner, a version and a release process, rather than something each person assembles.

| Topic | Today | With the dev image |
| --- | --- | --- |
| Setup | Install apps by hand from a wiki | Pull one image, reopen in container |
| Versions | Whatever each person has | Pinned in the image tag |
| Updating a tool | Everyone upgrades on their own | One PR, one release, teams adopt the tag |
| AWS access | Individually configured | Preconfigured through awshelper |
| Bug reproduction | "Which version do you have?" | Same image tag, same result |
| Ownership | Nobody in particular | Platform team owns the image |

**Roles.**

- **Platform team:** owns the Dockerfile, pipeline, Nexus repos and release notes.
- **Developers:** request tool changes by PR, pin a tag, and keep project code in the repo, not in the container.
- **Testers:** use the tag under test and record it in every bug report.

**Rollout.**

1. Pilot with one dev team and one tester for two weeks, collecting friction points.
2. Fix issues, publish the first stable tag, and write the quick-start from this document.
3. Onboard remaining Mac users team by team, with a short walkthrough session.
4. Add Windows users once the Mac path is stable.
5. Retire the old manual setup wiki page after one full release cycle.

## Risks and open questions

| Risk | Mitigation |
| --- | --- |
| Apple Silicon Macs pull an image built for the wrong CPU | Build multi-arch images (arm64 and amd64) in Harness |
| Docker Desktop licensing | Confirm licensing for all Mac users, then deploy and update Docker Desktop through Jamf |
| Slow first pull of a large image | Keep the image lean, use layered builds, and cache Nexus close to users |
| Slow file access on Mac bind mounts | Use VirtioFS in Docker Desktop and keep heavy folders such as `node_modules` in volumes |
| Credentials leaking into the image | Never bake secrets, and scan the image for them in the pipeline |
| Users resist the change | Pilot first, keep the quick-start short, and offer a walkthrough |

**Open questions.**

- [ ] How do users reach the jump box today: SSM, SSH, or something else?
- [ ] How do users authenticate to AWS: SSO, static keys, or role assumption?
- [ ] Which tools and exact versions go in the first image?
- [ ] Does Nexus need SSO or token-based login for pulls?
- [ ] Is VPN required to reach both Nexus and the jump box?
- [ ] Who is on the platform team that owns the image?
- [ ] How many Windows users, and do they have WSL 2 available?

**Next steps.**

- [ ] Draft the Dockerfile and `devcontainer.json` from the existing templates
- [ ] Wire the Harness pipeline to build, scan and publish to Nexus
- [ ] Package awshelper and publish it to the Nexus PyPI repo
- [ ] Choose pilot team and tester
