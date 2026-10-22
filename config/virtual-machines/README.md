# Virtual Machines

Core exposes wrapper commands for VMware Fusion CLI tools after VMware Fusion
is installed manually. It does not own a disposable Linux security lab.

VMware Fusion is not installed by the flake because Broadcom requires an
authenticated download and Homebrew's `vmware-fusion` cask is disabled. Keep
the downloaded installer under a backed-up operator path rather than under
`/Applications`, `/opt/homebrew`, `/nix`, caches, or VM bundle directories.

Useful host commands:

```sh
vmrun
vmcli
vmrest
ovftool --version
monitor-temp --count 1
```

For Windows CLI testing, run a stable Windows 11 Arm VM in Fusion, enable
OpenSSH Server inside Windows, and execute shell-specific tests over SSH.

`monitor-temp` uses built-in macOS tools. On the tested M2 MacBook Air,
`powermetrics` exposes thermal pressure and SoC power, while IOKit exposes
labeled battery temperature fields. It does not invent a CPU die temperature
when macOS does not expose one through supported built-in tools.

Private security VM ownership lives outside core and is composed only by the
operator's private layer.
