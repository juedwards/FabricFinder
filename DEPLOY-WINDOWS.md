# Deploying FabricFinder on Windows 11

A step-by-step setup. Takes ~10 minutes, once.

**Before you start:** you must be on your managed work laptop, and you must have
been given access to the FabricFinder GitHub repo and the Azure
OpenAI key/endpoint values.

---

## Step 1 — Install the prerequisites (one time)

Open **PowerShell** (Start → type "PowerShell" → Enter) and run these three:

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Microsoft.msodbcsql.18
winget install -e --id Git.Git
```

Approve any prompts. If `winget` is blocked on your device, install the same
three from **Company Portal / Software Center** instead:
*Python 3*, *Microsoft ODBC Driver 18 for SQL Server*, and *Git*.

**Close PowerShell and open a new one** so the installs are picked up.

## Step 2 — Download the app

```powershell
cd $HOME\Documents
git clone https://github.com/juedwards/FabricFinder.git
cd FabricFinder
```

The first time, a browser window asks you to sign in to GitHub — use the account
that was given repo access.

## Step 3 — Set it up

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This creates the environment and installs everything. (It will tell you if
Python or the ODBC driver is missing.)

## Step 4 — Add your Azure OpenAI key

```powershell
notepad .env
```

Fill in the four values (provided by your team), then **save and close** Notepad.

## Step 5 — Run it

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The first time you ask a question, a browser opens — **sign in with your
`@microsoft.com` account**. That's it. Ask away:

```
you > What are the top 5 countries by total current MAU?
you > /chart histogram of tenant MAU in TX
```

Reports appear in the `reports` folder. Type `exit` to quit.

**Next time**, you only need Step 5 (`run.ps1`) — your sign-in is remembered.

---

## Troubleshooting

| Problem | Fix |
|--------|-----|
| `winget` not recognized | Update "App Installer" from the Microsoft Store, or install the three apps from Company Portal. |
| `python` not recognized after install | Close and reopen PowerShell (PATH needs a refresh). |
| `setup.ps1` says ODBC driver missing | Run `winget install -e --id Microsoft.msodbcsql.18` (may need admin / Company Portal). |
| Script "cannot be loaded because running scripts is disabled" | Use the exact command shown (`powershell -ExecutionPolicy Bypass -File ...`). |
| Sign-in error **530033** ("device must be managed") | You're not on a managed/compliant device — use your corp laptop. |
| Git asks for a password and fails | You haven't been granted repo access yet — ask Justin to add you. |
