# Rhythm Heaven Fever Access

An external companion app to make Rhythm Heaven Fever accessible to blind players

<!-- website-hide-start -->

## View this on my website

RH Fever Access is also available on my website (recommended for most users)

[View RH Fever Access on my website](https://molitvan.me/projects/rh-fever-access)

<!-- website-hide-end -->

## Important Note

This project is still very much a work in progress. While the main game can already be completed with this, support for extras like 2 player mode, rhythm toys etc. is still a work in progress. Also expect some stability issues.

## Installation

Note: you have to have Rhythm Heaven Fever running inside [Dolphin Emulator](https://dolphin-emu.org/) before following this
1. Download and install [Git](https://git-scm.com/install/windows) and [UV](https://docs.astral.sh/uv/getting-started/installation/#__tabbed_1_2). If you're using Windows 11, you can get these two through WinGet (easier method). Open Command Prompt and run this command: ```winget install Git.Git astral-sh.uv```
2. Open Command Prompt in the location where you want to install RH Fever Access
3. Clone the repository by running this command: ```git clone https://github.com/Molitvan/rh-fever-access```
4. Install dependencies by running this command: ```uv sync```
5. You can now run RH Fever Access with this command: ```uv run rhf-access```
6. Now run the game inside Dolphin, and it should speak

## Updates

To update the app, open Command Prompt in the folder where you cloned it and run this command: ```git pull```

## Features

Like most other Rhythm Heaven games, Rhythm Heaven Fever is only missing a screen reader for it to be fully accessible. This program adds that.

## Credits

This project wouldn't have been possible without the initial work done by [@KamiKitsune420](https://github.com/KamiKitsune420). A huge thanks for the work and for letting me continue this project.
