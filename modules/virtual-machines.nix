{ pkgs, ... }:

let
  vmwareFusionCli = ../libexec/virtual-machines/vmware-fusion-cli.sh;

  fusionCli = name:
    pkgs.writeTextFile {
      name = "vmware-fusion-${name}";
      destination = "/bin/${name}";
      executable = true;
      text = builtins.readFile vmwareFusionCli;
    };
in
{
  environment.systemPackages = [
    (fusionCli "vmcli")
    (fusionCli "vmrest")
    (fusionCli "vmrun")
    (fusionCli "ovftool")
  ];
}
