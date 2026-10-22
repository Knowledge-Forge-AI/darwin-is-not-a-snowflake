{ userName }:
{ ... }:

let
  terminalProfileName = "terminal_settings";
  terminalColumns = 90;
  terminalRows = 30;
  terminalFontWidthSpacing = "1.0";
  terminalFontHeightSpacing = "1.0";
  terminalFontName = "JetBrainsMonoNFM-Regular";
  terminalFontArchive = "YnBsaXN0MDDUAQIDBAUGBwpYJHZlcnNpb25ZJGFyY2hpdmVyVCR0b3BYJG9iamVjdHMSAAGGoF8QD05TS2V5ZWRBcmNoaXZlctEICVRyb290gAGkCwwVFlUkbnVsbNQNDg8QERITFFZOU1NpemVYTlNmRmxhZ3NWTlNOYW1lViRjbGFzcyNAKAAAAAAAABAQgAKAA18QGEpldEJyYWluc01vbm9ORk0tUmVndWxhctIXGBkaWiRjbGFzc25hbWVYJGNsYXNzZXNWTlNGb250ohkbWE5TT2JqZWN0CBEaJCkyN0lMUVNYXmdud36FjpCSlK+0v8jP0gAAAAAAAAEBAAAAAAAAABwAAAAAAAAAAAAAAAAAAADb";
in
{
  system.activationScripts.postActivation.text = ''
    echo "setting up Terminal profile..."

    user_uid=$(/usr/bin/id -u ${userName})

    run_as_user() {
      /bin/launchctl asuser "$user_uid" /usr/bin/sudo -u ${userName} "$@"
    }

    tmp_plist="$(run_as_user /usr/bin/mktemp -t terminal-settings)"

    if run_as_user /usr/bin/defaults export com.apple.Terminal "$tmp_plist" >/dev/null 2>&1; then
      /usr/bin/plutil -replace 'Window Settings.${terminalProfileName}.columnCount' -integer ${toString terminalColumns} "$tmp_plist"
      /usr/bin/plutil -replace 'Window Settings.${terminalProfileName}.rowCount' -integer ${toString terminalRows} "$tmp_plist"
      /usr/bin/plutil -replace 'Window Settings.${terminalProfileName}.FontWidthSpacing' -float ${terminalFontWidthSpacing} "$tmp_plist"
      /usr/bin/plutil -replace 'Window Settings.${terminalProfileName}.FontHeightSpacing' -float ${terminalFontHeightSpacing} "$tmp_plist"
      /usr/bin/plutil -replace 'Window Settings.${terminalProfileName}.Font' -data ${terminalFontArchive} "$tmp_plist"

      run_as_user /usr/bin/defaults import com.apple.Terminal "$tmp_plist"
      run_as_user /usr/bin/defaults write com.apple.Terminal "Default Window Settings" -string "${terminalProfileName}"
      run_as_user /usr/bin/defaults write com.apple.Terminal "Startup Window Settings" -string "${terminalProfileName}"
      /usr/bin/killall cfprefsd >/dev/null 2>&1 || true
    else
      echo "warning: could not export com.apple.Terminal preferences for ${userName}; leaving Terminal profile unchanged" >&2
    fi

    /bin/rm -f "$tmp_plist"
  '';
}
