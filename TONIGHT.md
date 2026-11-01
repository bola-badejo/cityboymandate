# Tonight: free hosting and @citybot_mandate (beginner guide)

You need a computer (not just a phone), a GitHub account, and about 90 minutes. Do Part A first. It cannot break.

## Part A. Free demo link (15 minutes, always on, no server)
1. Sign up at github.com and confirm your email.
2. Click + (top right) > New repository. Name: `cityboymandate-demo`. Choose Public. Tick "Add a README". Create.
3. Add file > Upload files. Drag in the file `index.html` from the download. Click Commit changes.
4. Settings > Pages > Source "Deploy from a branch" > Branch `main`, folder `/ (root)` > Save. Wait 2 minutes.
5. Your link: `https://YOURUSERNAME.github.io/cityboymandate-demo/`. It shows the dashboard with FICTIONAL demo data (a yellow badge says so). Always say so out loud when you present.

## Part B. Live server on Render (free, 40 minutes)
1. New GitHub repository `cityboymandate` (Public). Unzip `cityboymandate-deploy.zip` on your computer. Open the unzipped folder and drag EVERYTHING inside it
   (not the zip itself) into Add file > Upload files. Commit.
2. Sign up at render.com with "Sign in with GitHub". New > Blueprint > pick the repo > Apply. It asks for BOOTSTRAP_ADMIN_PASSWORD: type a long password (12+ characters)
   and write it down. If Render asks for payment details, stop: Part A is enough for tonight.
3. Wait for "Live" (3 to 6 minutes). Open `https://YOURNAME.onrender.com/health`. You should see {"status":"ok",...}.
4. Open `https://YOURNAME.onrender.com/dashboard/`. Sign in: username `admin`, your password. You should see sample items in the review queue.
5. Keep it awake: free servers sleep after 15 idle minutes (first visit then takes about a minute). Make a free monitor at uptimerobot.com or cron-job.org that opens
   `https://YOURNAME.onrender.com/health` every 5 minutes. Restarts wipe the data (the demo refills itself).

## Part C. Connect @citybot_mandate (optional, costs a few dollars of X credits)
1. console.x.com > sign in > create an app (use the use-case text you were given) > User authentication settings > Set up: app type "Web App, Automated App or Bot", permissions "Read and write",
   a callback URL and website URL. Billing > Credits: add a small amount. Save the Bearer Token somewhere private (never paste it in chat or on websites).
2. Find the bot's number. Windows PowerShell: `curl.exe -H "Authorization: Bearer YOUR_TOKEN" "https://api.x.com/2/users/by/username/citybot_mandate"`
   (Mac/Linux Terminal: `curl`). Copy the `id` number.
3. Render > your service > Environment > add: `DEMO_MODE`=0, `X_BEARER_TOKEN`=your token, `X_BOT_HANDLE`=citybot_mandate, `X_BOT_USER_ID`=the number. Save (it redeploys).
   Leave `X_KEYWORD_SEARCH`=0 and `SCAN_INTERVAL_SECONDS`=3600: mentions only, hourly. That is the cheapest way to run. Publishing stays OFF: the bot never posts by itself.
4. Test: from a different X account, reply "@citybot_mandate is this true?" under any post. In the dashboard click **Scan now**. A draft appears.
   With no document library loaded the draft honestly says it could not verify. That is expected.

## Presenting
Show Part A for the full picture (evidence highlighted in the source, the checklist that blocks a reply, rules, audit trail). Show Part C for the live proof: tag, Scan now, draft appears, a person approves.
Say clearly: demo data is fictional; nothing posts without human approval; replies are labelled automated; real verdicts need your library of official documents.

## If something goes wrong
- Render build failed: open the service > Logs, copy the last 30 lines and send them to me.
- Login says wrong password: it is the BOOTSTRAP_ADMIN_PASSWORD you typed in step B2 (Environment tab shows it).
- Mentions never appear: X credits at zero (error 402), wrong X_BOT_USER_ID, or you did not click Scan now.
