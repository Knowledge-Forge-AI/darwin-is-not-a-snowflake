{ ... }:

{
  services.openssh = {
    enable = true;
    extraConfig = ''
      PubkeyAuthentication yes
      AuthenticationMethods publickey

      PasswordAuthentication no
      KbdInteractiveAuthentication no
      ChallengeResponseAuthentication no
      PermitEmptyPasswords no

      PermitRootLogin no
    '';
  };
}
