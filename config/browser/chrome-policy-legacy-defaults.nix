# Checked-in structured representation of legacy Chrome managed policy.
# Matches config/browser/chrome-policy-legacy.plist via semantic comparison.
# Retained by R3 on the target and by the predecessor for CNT/storage continuity.
{
  AudioCaptureAllowedUrls = [
    "https://meet.google.com/"
    "https://[*.]zoom.us/"
    "https://[*.]zoomgov.com/"
    "https://teams.microsoft.com/"
    "https://[*.]teams.microsoft.com/"
    "https://teams.live.com/"
    "https://teams.cloud.microsoft/"
    "https://[*.]webex.com/"
    "https://[*.]goto.com/"
    "https://[*.]gotomeeting.com/"
    "https://[*.]ringcentral.com/"
    "https://[*.]whereby.com/"
    "https://meet.jit.si/"
    "https://doxy.me/"
    "https://[*.]doxy.me/"
    "https://[*.]simplepractice.com/"
    "https://[*.]therapynotes.com/"
    "https://[*.]mdlive.com/"
    "https://[*.]amwell.com/"
    "https://[*.]teladoc.com/"
    "https://[*.]teladochealth.com/"
  ];
  BlockThirdPartyCookies = true;
  DefaultAutomaticDownloadsSetting = 3;
  DefaultGeolocationSetting = 3;
  DefaultMediaStreamSetting = 3;
  DefaultNotificationsSetting = 3;
  DnsOverHttpsMode = "off";
  ExtensionInstallForcelist = [
    "felphkbfjadmcejnibcmcncimlappdde;https://clients2.google.com/service/update2/crx"
  ];
  ExtensionSettings = {
    "*" = {
      installation_mode = "allowed";
    };
    "ckkdlimhmcjmikdlpkmbgfkaikojcbjk" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "ddkjiahejlhfcafbddmgiahcphecmpfh" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "efaidnbmnnnibpcajpcglclefindmkaj" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "efknglbfhoddmmfabeihlemgekhhnabb" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "felphkbfjadmcejnibcmcncimlappdde" = {
      installation_mode = "force_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "hlkenndednhfkekhgcdicdfddnkalmdm" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "hoombieeljmmljlkjmnheibnpciblicm" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "lmjnegcaeklhafolokijcfjliaokphfk" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
    "oboonakemofpalcgghocfoadofidjkkk" = {
      installation_mode = "normal_installed";
      update_url = "https://clients2.google.com/service/update2/crx";
    };
  };
  HttpsOnlyMode = "force_balanced_enabled";
  VideoCaptureAllowedUrls = [
    "https://meet.google.com/"
    "https://[*.]zoom.us/"
    "https://[*.]zoomgov.com/"
    "https://teams.microsoft.com/"
    "https://[*.]teams.microsoft.com/"
    "https://teams.live.com/"
    "https://teams.cloud.microsoft/"
    "https://[*.]webex.com/"
    "https://[*.]goto.com/"
    "https://[*.]gotomeeting.com/"
    "https://[*.]ringcentral.com/"
    "https://[*.]whereby.com/"
    "https://meet.jit.si/"
    "https://doxy.me/"
    "https://[*.]doxy.me/"
    "https://[*.]simplepractice.com/"
    "https://[*.]therapynotes.com/"
    "https://[*.]mdlive.com/"
    "https://[*.]amwell.com/"
    "https://[*.]teladoc.com/"
    "https://[*.]teladochealth.com/"
  ];
}
