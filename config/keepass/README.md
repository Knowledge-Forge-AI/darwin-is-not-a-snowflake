# KeePass Hardening Notes

This directory is documentation-only. Do not place live KeePassXC or
KeePass2Android secrets here.

## Files Not To Manage In Git Or Nix

- `~/Library/Application Support/KeePassXC/keepassxc.ini`
- `~/Library/Preferences/org.keepassxc.keepassxc.plist`
- KeePass key files, usually `*.key` or similar
- Any exported database, HTML, CSV, XML, or emergency recovery copy

`keepassxc.ini` is an application settings file, but it can contain KeeShare
private signing key material when KeeShare has generated a signing certificate.
Treat it as sensitive unless you have inspected the exact file and removed that
material.

## KeePassXC On macOS

Use the Homebrew cask managed by nix-darwin instead of `pkgs.keepassxc`. The
upstream macOS app is Developer-ID signed, which is important for macOS Keychain
and Touch ID / Apple Watch Quick Unlock behavior.

Quick Unlock is convenience state, not a replacement for the database master
key. Expect to enter the full database credentials after KeePassXC quits,
restarts, or is upgraded.

If you do not use KeeShare:

1. Open KeePassXC.
2. Go to Tools -> Settings -> KeeShare.
3. Disable import and export.
4. Remove any generated signing certificate if the UI offers that action.
5. Quit KeePassXC and confirm `keepassxc.ini` no longer contains a
   `[KeeShare]` `Own=` value before considering the file non-sensitive.

## KeePass2Android

Fingerprint / QuickUnlock is local convenience state on the phone. Do not rely
on it as your only way back into the database. KeepPass2Android's own
documentation says to ensure you know the master password before moving devices,
because fingerprint unlock will not be immediately available on a new device.

If using a key file with KeePass2Android:

- Keep the key file out of the same cloud-synced folder as the KDBX database.
- Transfer the key file to each trusted device through a separate channel.
- Back it up offline; losing the key file is equivalent to losing part of the
  database password.

## Re-Keying / Re-Encrypting The Database

Before changing anything:

1. Make an offline backup of the current `.kdbx`.
2. Make a separate offline backup of any current key file.
3. Verify the backup opens before changing the live database.

To rotate the database master key in KeePassXC:

1. Open the database with the current credentials.
2. Go to Database -> Database Settings -> Security.
3. Change the master password and/or add or replace the key file.
4. Save the database.
5. Lock and reopen it with the new credentials on the Mac.
6. Copy the updated key file to the phone through a separate channel if needed.
7. Open the updated database in KeePass2Android with the new credentials.

Only delete old passwords, old key files, or old database copies after the new
combination has been tested on every device that must keep access.
