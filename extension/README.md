# TrueFalse Chrome extension

Right-click any text, link, image or page and pick **Check with TrueFalse**. The answer opens
in a small window: verdict, short reason, sources, a message you can copy and forward, and
👍 / 👎 Report a mistake. The toolbar button lets you paste something to check.

## Install (developer mode)

1. Start the backend (see the main README). By default the extension talks to
   `http://localhost:8000`.
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and pick
   this `extension/` folder.
3. To use a different server, open the extension's **Settings** and enter its address.

## Notes

- Uses the same web API as the PWA (`POST /api/check`, `GET /api/check/{id}`,
  `POST /api/check/{id}/feedback`) and an anonymous random id for rate limiting.
- Images are downloaded and uploaded when the site allows it; otherwise the image link is sent.
- No build step and no tracking. Nothing is sent anywhere except the server you set.
