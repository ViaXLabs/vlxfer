setup a VL cyber dodjo

Clarifying how the dojo setup relates to the dev container.
Clarifying how the dojo setup relates to the dev container.
Right, they’re independent. The dojo is a web platform (CTFd plus per-user challenge containers), and a dev container is a VS Code feature for your local environment. Neither depends on the other. I paired them because they cover different jobs: the dojo hosts challenges and scoring, and the dev container is the shared toolbox your team uses to work on them.

The only link between them is how you reach challenges. The dojo runs each challenge in its own container, and you connect to it over SSH or its web workspace. You can SSH into it from the dev container terminal or from VS Code’s Remote-SSH, but nothing requires that.

If you want just one:

Dev container only gives you a shared tool environment, with no scoring, accounts or hosted challenges.
Dojo only gives you the full pwn.college experience. As I recall, its web workspace already includes an in-browser VS Code and terminal, so you may not need a local dev container at all. I haven’t confirmed that for the current version.
For a small team like you and Josh, I’d start with the dev container (cheap and quick), then add the dojo once you have challenges worth hosting. Do you want the dojo and dev container kept separate in the repo, or should I trim the zip to one of them?

Dojo

Ran a command, shared files
I trimmed cyber-dojo.zip to the dojo only. The dev container, Dockerfile and image-build workflow are gone.

What’s in it

dojo/deploy.sh runs the platform.
dojo-content/ is a starter dojo with one ret2win challenge.
scripts/new-challenge.sh scaffolds more challenges.
The README has the setup steps.
To get running

Get a Linux host. A cheap VPS is best for sharing with Josh. On your Mac, an Ubuntu 22.04 VM works, and the README has the Multipass commands.
Run ./dojo/deploy.sh on that host, then watch the first boot with docker exec dojo dojo logs.
Log in as admin and create your dojo at /dojos/create, pointing it at a git repo containing dojo-content/.
It still hasn’t been deployed end to end, and the dojo.yml format is from memory. Check it against pwncollege/example-dojo first.

Do you want to run it on a Mac VM or a VPS? I can tailor the steps, including a reverse proxy and HTTPS if it’s going on a server.


Cyber dojo
ZIP 
why did you get rid of the Docker stuff? And the dev container. Yeah, not the VS Code stuff for the dev image, but the container. I thought this is all Dockerized and stuff. I don't know anything about this Dojo thing, so I'm really leaning on you here. How can actually this help us? So explain that. Like, why do we need to use this?

Clarifying the confusion about Docker and the dev setup.
Clarifying the confusion about Docker and the dev setup.
The Docker part is still there. The dojo itself runs as a Docker container (that’s what dojo/deploy.sh starts), and it launches a separate container for each person’s challenge. What I removed was the VS Code dev container, the shared toolbox for your own machines. I read “Dojo” as “keep only the dojo”, and I should have checked. I can add the dev container back if you want both.

What a dojo is: a self-hosted training site for hacking skills, like a private version of pwn.college.

You write challenges, which are small deliberately vulnerable programs.
Each person clicks “Start” and gets their own private container with the challenge running, usually through a browser terminal or SSH.
They exploit the program to find a hidden flag and submit it for points.
Challenges are grouped into modules, with scoreboards and progress tracking.
How it helps you and Josh:

You can learn or practice without setting up vulnerable targets by hand, and it’s safe because everything is isolated.
It tracks who has solved what, which is useful for teaching or onboarding people.
You can write your own challenges and share them as a git repo.
Whether you need it: only if your goal is training or practice with challenges. If you just wanted a shared environment for real security work, a dev container alone would do that, and the dojo would be overkill.

What’s the goal here: learning together, training other people, or something for work?

