Render QR handling note

Current whatsapp/index.js QR handler logs a browser-openable data URL using qrcode.toDataURL().

How to scan:
1) Redeploy and find the log line containing 'Scan QR from this data URL (open in browser):'
2) Copy the full data URL value (starts with: data:image/png;base64, ...)
3) Paste into a browser address bar or open it in a new tab
4) Scan the QR from that browser page in WhatsApp

