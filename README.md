# Receipt Vault

The household's receipts in one place, on the home server, so they can be found years later.
Take a picture or forward an email; the Claude extension in Chrome reads them and finds them
again. Details and rules are in [CLAUDE.md](CLAUDE.md).

## On the laptop

`npm start`, then open http://127.0.0.1:8000. `npm test` and `npm run lint` run the checks.

## On the server, with Portainer

The app has no passwords, so keep it on your home network: **never forward port 8020 on your
router** and don't put it behind a public tunnel. Anyone on your Wi-Fi can open it.

### 1. Let Portainer download the app (once)

The image is private, like this repository.

1. On GitHub: Settings → Developer settings → Personal access tokens → Tokens (classic) →
   Generate new token. Tick only **`read:packages`**, and copy the token.
2. In Portainer: Registries → Add registry → Custom registry. Registry URL `ghcr.io`, turn on
   Authentication, username `graloop`, password: the token.

### 2. Start it

In Portainer: Stacks → Add stack → name it `receipt-vault` → Web editor → paste
[docker-compose.yml](docker-compose.yml) → Deploy the stack.

Forwarded email isn't needed for a first test. For it, see step 4.

### 3. Open it on your phone

1. Find the server's address on your network (the one you use for Portainer), and open
   `http://<server address>:8020` on your phone, on the home Wi-Fi.
2. Create your profile. In Settings, check the version at the bottom of the menu.
3. Add it to the home screen. iPhone: Safari → Share → Add to Home Screen. Android: Chrome → ⋮ →
   Add to Home screen.

### 4. Forwarded email (when you're ready)

1. Create a new Gmail address just for receipts, and turn on 2-Step Verification for it.
2. On that account, go to https://myaccount.google.com/apppasswords and create an app password.
3. In Portainer, open the stack → Editor → Environment variables, add these, then Update the stack:
   - `RECEIPTS_IMAP_USER`: the new address
   - `RECEIPTS_IMAP_PASSWORD`: the app password
4. In the app, under Settings → Profiles, type the email you'll forward *from* in your profile.
5. Forward a receipt to the new address, then press **Check the mailbox now** in Settings. After
   that, the app checks by itself once a day.

Mail from an address that isn't on a profile is ignored and left unread, and it's filed at a
later check if that address is added to a profile.

### Reading receipts

On the computer, open `http://<server address>:8020/?unread=1&scope=all` in Chrome and ask the
Claude extension:

> Read all the receipts on this page. Open each one, copy what the original shows into its
> fields, including all its text, then press "Save and next" until nothing is left to read.

To find one later: "Find the Amazon headphones receipt in this app."

### Making a new version

Push to `main`. GitHub checks the code, builds the image, tries it, and publishes it (Actions tab,
a few minutes). Then in Portainer: open the stack → Update the stack, with **Re-pull image** on.
The receipts are in the `receipts_data` volume and stay where they are; Settings shows the new
version number.

To go back: add `TAG=sha-xxxxxxx` to the stack's environment variables (the tags are listed under
Packages on GitHub) and update the stack again.

If the stack ever has to be removed, leave the `receipts_data` volume alone: it's all your receipts.
