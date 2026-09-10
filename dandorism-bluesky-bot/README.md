# Dandorism Bluesky Bot

This GitHub Actions bot publishes one item per day, seven days a week. It
chooses a random active item without repeats until the active backlog has been
used, then begins another randomized cycle. It also chooses a random posting
hour from 7 AM through 1 PM fixed EST.

## How daily operation works

GitHub is set to run the bot at 7:00, 8:00, 9:00, 10:00, 11:00, 12:00, and 1:00
fixed EST. At the first check, the bot reserves a random unused entry and one of
the remaining hours that day. At the selected hour it publishes that entry.
GitHub can start scheduled jobs late, so these are approximate times.

- A whole thread counts as the day's one selected item.
- If a thread fails partway through, its next attempt resumes at the failed part
  instead of repeating the parts that already published.
- New entries immediately join the unused pool in the current cycle.
- No entry repeats until all eligible entries have been used.
- The first entry of a new cycle cannot equal the previous cycle's last entry
  when at least two entries are active.
- A missed day does not cause several posts to publish later.
- Disabling or deleting a reserved entry safely cancels that reservation.

## Installation

1. Create a new **private** GitHub repository.
2. Upload all files and folders from this package. Preserve the
   `.github/workflows/` folders.
3. In Bluesky, open **Settings → Privacy and Security → App passwords** and
   create a separate app password for this bot.
4. In GitHub, open **Settings → Secrets and variables → Actions** and add:
   - `BLUESKY_HANDLE` — the complete handle, such as `dandorism.bsky.social`
   - `BLUESKY_APP_PASSWORD` — the separate app password from Bluesky
5. Never put a password in `posts.yml`, source code, screenshots, or commits.

The repository starts with automatic posting paused.

## Test and activate

1. Open the repository's **Actions** tab.
2. Select **Dandorism daily Bluesky post**, then **Run workflow**.
3. Leave **Validate only** checked. Optionally enter a post ID to inspect.
4. Review the successful workflow log.
5. To publish a real manual test, run it again with an active post ID and turn
   off **Validate only**. A manual publication counts as that day's post.
6. When satisfied, change `settings.enabled` in `posts.yml` to `true`.

Pause the bot at any time by changing it back to `false`:

```yaml
settings:
  enabled: false
```

## Master-list rules

All content lives under `posts:` in `posts.yml`.

- Every entry needs a permanent, unique `id`.
- IDs use lowercase letters, numbers, hyphens, and underscores only.
- Never reuse an old ID for unrelated content.
- `enabled` is optional and defaults to `true`.
- YAML indentation matters: use spaces, never tabs.
- `>-` makes multi-line YAML source become one readable paragraph.
- Each post or thread part can contain at most 300 user-perceived characters.
- The bot rejects overlong content; it never truncates or automatically splits it.
- Each individual post can use either images or a preview card, not both. This
  is a Bluesky embed limitation. Different parts of one thread may use
  different embed types.

### Plain text

```yaml
  - id: prepare-before-beginning
    text: >-
      Preparation is not separate from the work. It is what allows the work
      to move smoothly.
```

### Temporarily disabled entry

```yaml
  - id: seasonal-reminder
    enabled: false
    text: >-
      This entry remains in the library but cannot be selected.
```

Changing `enabled` alone does not release a quarantine because it does not fix
the content that failed.

### Clickable links

Complete `http://` and `https://` URLs in the text become clickable. Ordinary
links do not create a large preview card.

```yaml
  - id: useful-reference
    text: >-
      Read the complete reference at https://example.com/reference
```

### One image

Store images inside an `images/` folder in the repository. Every image requires
useful alt text and must be no larger than 1 MB.

```yaml
  - id: prepared-workbench
    text: >-
      The arrangement of the workspace shapes the movement of the work.
    images:
      - path: images/prepared-workbench.jpg
        alt: "A workbench with frequently used tools arranged within easy reach."
```

### Multiple images

Bluesky allows up to four images on one post. Give every image its own alt text.

```yaml
  - id: workspace-sequence
    text: >-
      Before, preparation, execution, and the cleared workspace afterward.
    images:
      - path: images/sequence-1.jpg
        alt: "An unorganized workbench before preparation."
      - path: images/sequence-2.jpg
        alt: "Tools grouped in the order they will be used."
      - path: images/sequence-3.jpg
        alt: "Work underway at the prepared bench."
      - path: images/sequence-4.jpg
        alt: "The workbench cleared after completing the task."
```

### Automatically populated preview card

At minimum, provide the URL. The bot attempts to retrieve the webpage title,
description, and preview image. If metadata cannot be retrieved, it uses the
site hostname as the title and publishes the card without a thumbnail.

```yaml
  - id: article-card-automatic
    text: >-
      A useful article about deliberate preparation.
    link_card:
      url: "https://example.com/article"
```

### Explicit preview card

Explicit fields are more predictable. The preview image is optional, stored in
the repository, and limited to 1 MB.

```yaml
  - id: article-card-explicit
    text: >-
      A useful article about deliberate preparation.
    link_card:
      url: "https://example.com/article"
      title: "The Art of Preparing the Work"
      description: "How deliberate preparation improves speed and consistency."
      image: images/article-preview.jpg
```

### Thread

A thread needs at least two parts. Each part is validated as a separate Bluesky
post and may contain its own clickable links, images, or preview card.

```yaml
  - id: three-parts-of-dandori
    thread:
      - text: >-
          Dandori can be understood through three connected practices. 🧵

      - text: >-
          First: arrange tools and resources before beginning.
        images:
          - path: images/arranged-tools.jpg
            alt: "Tools arranged in their expected order of use."

      - text: >-
          Second: determine the order of operations so each action prepares
          the conditions for the next.

      - text: >-
          Third: improve the arrangement after observing where friction occurred.
        link_card:
          url: "https://example.com/dandori"
```

## Editing, adding, and deleting entries

- **Add:** append another uniquely identified entry under `posts:`. It becomes
  immediately eligible in the current cycle.
- **Edit before use:** the current cycle eventually publishes the edited version.
- **Edit after use:** the edited version becomes eligible when the next cycle begins.
- **Disable:** set `enabled: false`; it is excluded without being deleted.
- **Delete:** remove its entire block. Never assign its old ID to different content.

## Failures, retries, and quarantine

The selected item remains reserved after a failed attempt. The bot retries it
at the next hourly check instead of choosing something else.

After three failed attempts:

1. The entry is quarantined and excluded from every future cycle.
2. The failure, attempt times, and latest error remain recorded in `state.json`.
3. A GitHub Issue identifies the entry and explains the failure.
4. No replacement is posted that day.
5. Normal random selection resumes the following day.

Changing meaningful content—text, thread parts, images, alt text, or card
details—automatically releases that entry from quarantine. The related GitHub
Issue closes automatically. Merely toggling `enabled` does not count as a repair.

Do not edit a reserved thread while only part of it has published. Let its retry
finish first; the bot blocks a mixed old-and-new thread to protect its structure.

Temporary failure Issues are also closed automatically after a successful retry.

## Files you should and should not edit

- **Edit `posts.yml`:** this is your master content library and pause control.
- **Add files under `images/`:** use these in image posts and explicit cards.
- **Do not edit `state.json` normally:** the bot owns its cycles, history,
  reservations, and quarantines.
- **Do not edit `.github/workflows/post-to-bluesky.yml` merely to change posts.**

If `state.json` has a Git conflict, keep the version from the bot's most recent
successful workflow unless you deliberately intend to reset history.

## Reading the state file

- `cycle`: current randomized pass through the backlog
- `used_ids`: entries already handled this cycle
- `last_post_id`: prevents an immediate repeat between cycles
- `reservation`: today's chosen entry, hour, and retry attempts
- `quarantined`: entries excluded after three failures
- `daily_results`: recent daily outcomes
- `history`: recent publication and failure audit trail

History retains the most recent 500 events and daily results retain 90 days so
the file does not grow forever.

## Stop or revoke the bot

- Pause content by setting `settings.enabled: false`.
- Disable the workflow from its page in GitHub Actions to stop all checks.
- Revoke its app password in Bluesky to immediately remove account access.
- Replace a compromised password by revoking it, creating another, and updating
  the `BLUESKY_APP_PASSWORD` repository secret.

## Local validation (optional)

With Python 3.11 or newer:

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python bot.py --dry-run
python bot.py --dry-run --post-id welcome-to-dandorism
python -m unittest discover -s tests
```

Local live publication requires the two environment variables described above.
Dry runs never publish or change scheduler state.

## Common problems

- **Invalid YAML:** check indentation and ensure tabs were not used.
- **Duplicate post ID:** give every entry a different permanent ID.
- **Over 300 graphemes:** shorten that part or intentionally write a thread.
- **Image missing or over 1 MB:** correct the path or resize the file.
- **Images plus preview card:** use one embed type in that part, or put them in
  separate thread parts.
- **Authentication failure:** verify the handle and replace the app password.
- **No post appears:** confirm global and per-entry `enabled` values, then inspect
  the latest workflow and any open Issue.
