# Kindle Dash

A "before you leave the house" dashboard for a jailbroken Kindle Paperwhite 2.
GitHub draws a 758x1024 image every 30 minutes; the Kindle downloads it and shows it.

## Setup, in order

### On GitHub
1. Create a new **public** repo called `kindle-dash` under avp-neilhuk. Public, so Actions minutes are free (48 runs a day would use up most of a private repo's allowance). The API keys stay hidden as secrets.
2. Upload everything in this folder to it, keeping the folder structure (including `.github/workflows/dash.yml`).
3. Settings > Pages > Source: choose **GitHub Actions**.
4. Get the two free keys:
   - **TomTom** (roads): sign up at developer.tomtom.com and copy the default API key.
   - **Rail** (trains): sign up at raildata.org.uk, subscribe to the "Live Departure Board" product, and copy the consumer key.
5. Settings > Secrets and variables > Actions > New repository secret: add `TOMTOM_KEY` and `RAIL_KEY`.
6. Actions tab > Render dashboard > Run workflow. After about a minute,
   `https://avp-neilhuk.github.io/kindle-dash/dash.png` should open in a browser.

### On the Kindle (KUAL must already be installed)
7. Plug the Kindle in and copy `kindle/extensions/kindledash` into the Kindle's `extensions` folder.
8. If your GitHub username isn't avp-neilhuk, edit the URL in `dash.conf`.
9. Eject it, connect to Wi-Fi, open KUAL > Kindle Dash > **Test: show dashboard once**.
   Press the power button twice to get back to normal.
10. When the test works: KUAL > Kindle Dash > **Start dashboard**.
11. To exit, hold the power button for about 15 seconds. The Kindle restarts as a normal Kindle.

## Changing things
- Bin pattern: `BIN_ANCHOR` in `render.py` (a Thursday that is black + green week).
- Refresh rate and low-battery level: `dash.conf`.
- Preview without the Kindle: `python render.py --mock` writes `out/dash.png`.
