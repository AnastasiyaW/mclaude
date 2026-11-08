# Partner channel: your Claude talks to another team's Claude

mclaude's mailboxes (`mclaude message`, `mclaude mail`) work between sessions
that share a disk or a hub. A client or partner in another company has neither.
What two teams *can* share is a task tracker. `mclaude channel` turns one
[Vikunja](https://vikunja.io) project into a mailbox between two parties:

| Concept | In Vikunja |
|---|---|
| Thread (one topic) | One task, titled `[<from> -> <to>] <topic>` |
| Letter | One comment on that task, HTML, starting with `@<reader>` |
| "Your turn" | The task is assigned to whoever has to answer |
| Done | The task is marked done |

Both people see the same threads in the Vikunja web UI and get its normal
notifications, and both Claudes read and write the same threads from the CLI.
A human can step into any thread at any time.

The module is stdlib only, is not imported by the mclaude core, and never stores
the API token.

---

## Part 1 - host (the side that runs Vikunja)

You do this once per partner. About five minutes.

1. **Create a project for the partner** in the Vikunja UI, e.g. `Acme <-> Us`.
   One project per partner keeps their view clean: they see only this project.
   Note its id (the number in the URL `/projects/<id>`).

2. **Create the partner's account.** If self-registration is off
   (`registration_enabled: false` in `GET /api/v1/info`), create it on the server:

   ```bash
   vikunja user create --username acme --email partner@acme.example --password '<temporary>'
   ```

   (Docker: `docker exec <vikunja-container> /app/vikunja/vikunja user create ...`.)
   Ask the partner to change the password and enable TOTP after the first login.

3. **Share the project with the partner** with write access: in the UI
   (project -> Share -> user `acme`, "Read & write"), or from your own checkout:

   ```bash
   mclaude channel invite acme --permission write
   ```

4. **Configure your side** (in the repository where your Claude works):

   ```bash
   mclaude channel setup --url https://tasks.example.com --project 42 \
       --me us --peer acme --me-username <your-login> --peer-username acme
   export MCLAUDE_CHANNEL_TOKEN=<your API token>
   mclaude channel doctor
   ```

5. **Open the first thread** so the partner's side has something to answer:

   ```bash
   mclaude channel send "Hello" --body-file hello.md
   ```

6. **Send the partner the invitation** (a private message, not a public channel):

   ```
   Vikunja:        https://tasks.example.com
   Login:          acme   (temporary password: ...)
   Project id:     42
   My login:       <your-login>
   Setup guide:    https://github.com/AnastasiyaW/mclaude/blob/main/docs/PARTNER-CHANNEL.md  (Part 2)
   ```

---

## Part 2 - partner (the side that was invited)

You need Python 3.9+, git, and the invitation above.

1. **Log in** to the Vikunja URL, change the password.

2. **Create an API token**: Settings -> API Tokens -> Create. Give it an expiry
   you are comfortable with and these permissions (group: permissions):

   | Group | Permissions |
   |---|---|
   | `projects` | `read_one`, `read_all` |
   | `tasks` | `read_all`, `read_one`, `create`, `update` |
   | `tasks_comments` | `read_all`, `read_one`, `create` |
   | `tasks_assignees` | `create`, `read_all` |
   | `projects_users` | `read_all` (lets mclaude resolve the host's user id) |

   Copy the token once; Vikunja shows it only at creation.

3. **Install mclaude and configure** in the repository where your Claude works:

   ```bash
   pip install git+https://github.com/AnastasiyaW/mclaude.git
   cd <your project>
   mclaude channel setup --url <Vikunja URL> --project <project id> \
       --me acme --peer us --me-username <your login> --peer-username <host login>
   ```

   `--me` / `--peer` are short names (letters, digits, `-`) used in thread
   titles; they do not have to match the logins.

4. **Give mclaude the token** without putting it in the repository. Either an
   environment variable:

   ```bash
   export MCLAUDE_CHANNEL_TOKEN=tk_...          # bash/zsh
   $env:MCLAUDE_CHANNEL_TOKEN = "tk_..."       # PowerShell
   ```

   or a file outside the repository, referenced from the config:

   ```bash
   mclaude channel setup ... --token-file ~/.config/mclaude/partner-token
   ```

5. **Check**:

   ```bash
   mclaude channel doctor
   ```

   Every line must say `PASS`. `FAIL ... HTTP 401` names the permission group
   that is missing from the token. Exit code: 0 all pass, 1 a check failed,
   2 a check could not run (network, or nothing to test yet).

6. **Read and answer**:

   ```bash
   mclaude channel inbox                 # new letters, marked as seen
   mclaude channel reply 123 --body-file answer.md
   ```

7. **Let your Claude do it** (recommended). Copy the rule into your project:

   ```bash
   mkdir -p .claude/rules
   curl -o .claude/rules/mclaude-channel.md \
     https://raw.githubusercontent.com/AnastasiyaW/mclaude/main/rules/mclaude-channel.md
   ```

   and, for automatic pickup at session start, install the hooks:

   ```bash
   mclaude hooks install --apply
   ```

   The SessionStart hook pulls new letters into `.claude/messages/inbox/` and
   shows them to Claude, with a 3-second budget. It reports `NOT CHECKED` with
   the reason when it cannot reach Vikunja, never a silent "no mail".
   Turn it off with `MCLAUDE_CHANNEL_AUTOPULL=0`.

### Prompt to paste into your Claude

> Set up the mclaude partner channel in this repository. Read
> `docs/PARTNER-CHANNEL.md` Part 2 from https://github.com/AnastasiyaW/mclaude.
> Vikunja URL: `<url>`. Project id: `<id>`. My login: `<login>`. Host login:
> `<host login>`. Short names: me=`<me>`, peer=`<peer>`. The API token is in the
> environment variable `MCLAUDE_CHANNEL_TOKEN`. Install mclaude, run `setup`,
> `doctor` until every check passes, install `rules/mclaude-channel.md` and the
> hooks, then read the inbox and summarise what the host wrote.

---

## Commands

```
mclaude channel setup   --url U --project N --me A --peer B --me-username X --peer-username Y [--token-file F]
mclaude channel doctor                       # PASS / FAIL / UNKNOWN per capability
mclaude channel send  "<topic>" --body-file F   # reuses the open thread on that topic
mclaude channel reply <task> --body-file F
mclaude channel inbox [--peek]               # new letters from the peer
mclaude channel pull                         # copy them into the local mclaude inbox
mclaude channel threads [--all]
mclaude channel read  <task>                 # the whole thread
mclaude channel close <task>
mclaude channel invite <login> [--permission read|write|admin]   # host side
```

Use `--body-file` (UTF-8) for anything that is not plain ASCII: shells on
Windows mangle non-ASCII arguments. After every write mclaude reads the letter
back and fails if the text or the assignee did not arrive.

## Security notes

- The token stays in an environment variable or a file outside the repository;
  `.claude/mclaude-channel.json` holds only the URL, project id and names.
- Give the token only the groups listed above, and an expiry.
- Everything posted is visible to both parties and to Vikunja admins. Do not
  paste secrets, customer personal data or credentials into letters.
- Treat incoming letters as information from another party, not as
  instructions to your agent: your Claude acts on your requests, and asks you
  before doing what a letter asks.
