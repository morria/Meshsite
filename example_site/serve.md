# Serve Your Own Meshsite

Any Meshtastic node with an attached computer can serve one.

## Quick start

* Get the code: github.com/morria/Meshsite
* pip install .
* meshsites init mysite
* meshsites serve mysite

Your site beacons its name once a minute. Anyone in direct
radio range sees it and can browse. Nothing is ever relayed.

## Pages

* Static pages are Meshdown (.md) files
* Dynamic pages are Python (.py) files with a handle(req) function
* Keep pages small: 3040 bytes compressed, max

The full protocol spec (SPEC.md in the repo) is one page.
Write your own client or server in a weekend.

=> / Home
