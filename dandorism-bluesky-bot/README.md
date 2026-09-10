# Dandorism Bluesky Scheduler

A small GitHub Actions bot that publishes only the posts you write. It supports
scheduled text, clickable URLs, one optional image with alt text, duplicate
prevention, dry runs, and manual test posts.

## 1. Create the private GitHub repository

1. Create a new **private** repository on GitHub.
2. Upload every file and folder from this package, preserving `.github/workflows/`.
3. Edit `posts.yml` and replace the two examples with your writing.

Post IDs must be unique and should never be reused, even after removing an old
post. Times without an explicit UTC offset use `America/New_York`, including
automatic daylight-saving-time changes.

```yaml
- id: unique-post-name
  scheduled_at: "2026-09-20 10:17"
  text: >-
    Your complete post goes here. https://example.com links become clickable.
  image: images/optional-picture.jpg
  image_alt: "Describe the meaningful visual content of the picture."
```

Delete the `image` and `image_alt` lines for a text-only post. Images must be
stored inside the repository and no larger than 1 MB.

## 2. Create a Bluesky app password

In Bluesky, open **Settings → Privacy and Security → App passwords**, create an
app password for this bot, and copy it. This keeps your main account password
out of the automation. If the credential is ever exposed, revoke that app
password in Bluesky and create another.

## 3. Add the two GitHub secrets

Open the repository's **Settings → Secrets and variables → Actions**, then add:

- `BLUESKY_HANDLE`: your full Bluesky handle, such as `dandorism.bsky.social`
- `BLUESKY_APP_PASSWORD`: the app password created above

Never put either value in `posts.yml`, `bot.py`, a screenshot, or a commit.

## 4. Test without posting

1. Open the repository's **Actions** tab.
2. Select **Post scheduled Bluesky content**.
3. Choose **Run workflow**.
4. Leave **Validate without publishing** checked.

The log will either list due posts or say that none are due. To test a future
entry, enter its ID in the optional field while keeping dry run enabled.

## 5. Publish one test

Create a harmless test entry in `posts.yml`. Run the workflow manually, enter
that entry's ID, and turn off **Validate without publishing**. After success,
the bot writes its URI and timestamp to `state.json`.

## Normal operation

GitHub checks four times per hour. Any unposted entry whose time has passed is
published. A post may therefore appear several minutes after its requested
time. The workflow uses minutes away from the top of the hour to reduce GitHub
scheduler congestion.

If several overdue posts exist, they publish in chronological order during the
same run. Move their dates forward if you do not want a backlog released at
once.

## Editing and troubleshooting

- `No unposted entries are due`: the queue is healthy, but no scheduled time has passed.
- `Missing queue file`: confirm `posts.yml` is in the repository root.
- Authentication failure: confirm both secrets, then regenerate the app password if needed.
- Image failure: verify the path, file type, 1 MB limit, and alt text.
- Duplicate ID: give every entry a permanently unique ID.

To stop all automatic posting, open **Actions**, select the workflow, use its
menu, and choose **Disable workflow**. Revoking the Bluesky app password also
removes the bot's access immediately.

## Local validation (optional)

With Python 3.11 or newer installed:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python bot.py --dry-run --post-id welcome-to-dandorism
python -m unittest discover -s tests
```
