# WhatsApp

## From your own WhatsApp (no setup)

Add contacts in Ecosystem → Accounts → WhatsApp (name and number with country code), then ask
"WhatsApp Mom that I'm on my way".

- `whatsapp_send_personal`: ORION shows the recipient and exact text and asks yes or no (card, phone, or a
  spoken yes for voice requests). After a yes it opens **WhatsApp Desktop** on that chat with the message typed
  in and presses Enter, only while WhatsApp is the active window, then checks the message left the compose box.
  This briefly brings WhatsApp to the front. Needs WhatsApp Desktop (Microsoft Store); one-line messages only.
- `whatsapp_draft`: opens WhatsApp (or WhatsApp Web) with the message typed in and leaves Send to you.

Both use WhatsApp's own `whatsapp://send` / `wa.me` links: no unofficial client, nothing that risks your account.

## From a business number, in the background (Cloud API, optional)

ORION can also send through Meta's official **WhatsApp Business Cloud API**. It is a plain HTTPS
call, so it runs in the background: no WhatsApp window, no mouse, no focus change.

**Every message waits for you.** `whatsapp_send` shows the recipient and the exact text (or template) on an
approval card, also pushed to a paired phone, and only sends after you approve that one message. This holds
even with approval prompts turned off in Settings, and "Allow for this session" is never offered for it.

## What Meta's rules mean for you

- Messages come from a **WhatsApp Business number** registered in Meta, not from your personal WhatsApp.
- **24-hour window:** free text only reaches someone who messaged that business number in the last 24 hours.
  Otherwise only a pre-approved **message template** is delivered. Ask ORION to use a template, or it will
  tell you when WhatsApp refuses free text. `whatsapp_templates` lists yours.
- ORION reports a message as *accepted by WhatsApp*, with its message id. Delivery and read receipts come by
  webhook, which ORION does not run yet, so delivery is reported as unconfirmed.

## Setting it up

1. Create a Meta developer account at developers.facebook.com and a **Business** type app.
2. Add the **WhatsApp** product to the app. Meta gives you a free **test number** and a WhatsApp Business
   Account. While testing, add the numbers you want to message under WhatsApp → API Setup → *To* (up to 5).
3. From **WhatsApp → API Setup**, copy the **Phone number ID** and the **WhatsApp Business Account ID**.
4. Create a **permanent access token**: Business Settings → Users → System users → add a system user, assign
   it the app and the WhatsApp account, then *Generate token* with `whatsapp_business_messaging` and
   `whatsapp_business_management`. (The temporary token on API Setup expires after 24 hours.)
5. In ORION: Ecosystem → Accounts → WhatsApp, paste the IDs and token and press **Connect WhatsApp**. ORION
   checks them with Meta before saving. The token goes into Windows Credential Manager, never into a file
   ORION shows or logs.
6. Add contacts by name and number (with country code). Then ask: "WhatsApp Mom that I'm running late".

To send to anyone (not just the 5 test numbers), add your own phone number to the WhatsApp account in Meta and
verify your business. **Try a dry run first** builds and approves the exact message without contacting Meta.
