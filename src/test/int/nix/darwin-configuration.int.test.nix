# nix-darwin-test-source-target: flake.nix
# nix-darwin-test-source-target: modules/android.nix
# nix-darwin-test-source-target: modules/brave-youtube.nix
# nix-darwin-test-source-target: modules/brewfile-stragglers.nix
# nix-darwin-test-source-target: modules/browsers.nix
# nix-darwin-test-source-target: modules/codex-state.nix
# nix-darwin-test-source-target: modules/codex.nix
# nix-darwin-test-source-target: modules/deployment-effects.nix
# nix-darwin-test-source-target: modules/desktop-apps.nix
# nix-darwin-test-source-target: modules/developer-tooling.nix
# nix-darwin-test-source-target: modules/dinas/command-namespaces.nix
# nix-darwin-test-source-target: packages/dinas-commands/default.nix
# nix-darwin-test-source-target: lib/paths.nix
# nix-darwin-test-source-target: modules/fastfetch.nix
# nix-darwin-test-source-target: modules/host-security.nix
# nix-darwin-test-source-target: modules/kitty.nix
# nix-darwin-test-source-target: modules/nixpkgs-policy.nix
# nix-darwin-test-source-target: modules/nvm.nix
# nix-darwin-test-source-target: modules/oh-my-zsh.nix
# nix-darwin-test-source-target: modules/password-sync.nix
# nix-darwin-test-source-target: modules/pyenv.nix
# nix-darwin-test-source-target: modules/rbenv.nix
# nix-darwin-test-source-target: modules/sdkman.nix
# nix-darwin-test-source-target: modules/shell.nix
# nix-darwin-test-source-target: modules/ssh.nix
# nix-darwin-test-source-target: modules/terminal.nix
# nix-darwin-test-source-target: modules/video-session.nix
# nix-darwin-test-source-target: modules/virtual-machines.nix
# nix-darwin-test-source-target: packages/arq_restore/default.nix
# nix-darwin-test-source-target: packages/rtk/default.nix
# nix-darwin-test-source-target: packages/repomap-kg/default.nix
# nix-darwin-test-source-target: packages/dinas-commands/default.nix
{ repoRoot, system }:

let
  flake = builtins.getFlake "path:${toString repoRoot}";
  pkgs = flake.legacyPackages.${system};
  lib = pkgs.lib;
  config = flake.darwinConfigurations.example-primary.config;
  laptopConfig = flake.darwinConfigurations.example-workstation.config;
  packageNames = builtins.map lib.getName config.environment.systemPackages;
  fontNames = builtins.map (package: package.name or "") config.fonts.packages;
  caskNames = builtins.map (cask: cask.name) config.homebrew.casks;
  brewNames = builtins.map (brew: brew.name) config.homebrew.brews;
  tapNames = builtins.map (tap: tap.name) config.homebrew.taps;
  activationText = config.system.activationScripts.postActivation.text;
  shellConfig = config.system.build.dinasShellConfig;
  hasPackage = name: builtins.elem name packageNames;
  hasFontPrefix = prefix: builtins.any (name: lib.hasPrefix prefix name) fontNames;
  allIn = expected: actual: builtins.all (name: builtins.elem name actual) expected;
  hasActivationSnippets = snippets: builtins.all (snippet: lib.hasInfix snippet activationText) snippets;
  moduleSource = name: builtins.readFile (repoRoot + "/modules/${name}.nix");
  moduleHasSnippets = name: snippets: builtins.all (snippet: lib.hasInfix snippet (moduleSource name)) snippets;
  serviceSubset = serviceConfig: {
    ProgramArguments = serviceConfig.ProgramArguments;
    EnvironmentVariables = serviceConfig.EnvironmentVariables or { };
    RunAtLoad = serviceConfig.RunAtLoad;
    StartInterval = serviceConfig.StartInterval;
    StandardOutPath = serviceConfig.StandardOutPath;
    StandardErrorPath = serviceConfig.StandardErrorPath;
  };
  agentSubset = serviceConfig: {
    ProgramArguments = serviceConfig.ProgramArguments;
    RunAtLoad = serviceConfig.RunAtLoad;
    StartInterval = serviceConfig.StartInterval;
    StandardOutPath = serviceConfig.StandardOutPath;
    StandardErrorPath = serviceConfig.StandardErrorPath;
    WorkingDirectory = serviceConfig.WorkingDirectory;
  };
  retiredRepoMapPackages = [
    "repo-map-runner"
    "repo-map-manager"
    "repo-map-orchestrator"
    "repo-map-orch32-contract"
    "repo-map-orch32-loopback-adapter"
    "repo-map-orch32-worker-rootfs"
    "repo-map-orch33-launchers"
  ];
  retiredRepoMapAgents = [
    "repo-map-runner-run-next"
    "repo-map-manager-list-unreviewed"
    "repo-map-orchestrator-prepare-reviews"
    "repo-map-orchestrator-prepare-review-bundles"
  ];
  retiredRepoMapNewsyslogEntries = [
    "newsyslog.d/com.Knowledge-Forge-AI.repo-map-runner.run-next.conf"
    "newsyslog.d/com.Knowledge-Forge-AI.repo-map-manager.list-unreviewed.conf"
    "newsyslog.d/com.Knowledge-Forge-AI.repo-map-orchestrator.prepare-reviews.conf"
    "newsyslog.d/com.Knowledge-Forge-AI.repo-map-orchestrator.prepare-review-bundles.conf"
  ];
  retiredRepoMapActivationNeedles = [
    ".local/state/repo-map-runner"
    ".local/state/repo-map-manager"
    ".local/state/repo-map-orchestrator"
    ".local/config/repo-map-runner"
    ".local/config/repo-map-manager"
    ".local/config/repo-map-orchestrator"
    "/Library/Application Support/repo-map/orch32"
    "repo-map-orch32-contract"
  ];
in
{
  testRetiredRepoMapPackagesAreAbsentFromCoreSystemPackages = {
    expr = {
      macBookPro = builtins.all
        (name: !(builtins.elem name packageNames))
        retiredRepoMapPackages;
      laptop =
        let
          laptopPackageNames = builtins.map
            lib.getName
            laptopConfig.environment.systemPackages;
        in
        builtins.all
          (name: !(builtins.elem name laptopPackageNames))
          retiredRepoMapPackages;
    };
    expected = {
      macBookPro = true;
      laptop = true;
    };
  };

  testRetiredRepoMapLaunchdAndNewsyslogConfigurationIsAbsent = {
    expr = {
      macBookProUserAgents = builtins.all
        (name: !(builtins.hasAttr name config.launchd.user.agents))
        retiredRepoMapAgents;
      macBookProSystemAgents = builtins.all
        (name: !(builtins.hasAttr name config.launchd.agents))
        retiredRepoMapAgents;
      laptopUserAgents = builtins.all
        (name: !(builtins.hasAttr name laptopConfig.launchd.user.agents))
        retiredRepoMapAgents;
      laptopSystemAgents = builtins.all
        (name: !(builtins.hasAttr name laptopConfig.launchd.agents))
        retiredRepoMapAgents;
      macBookProNewsyslog = builtins.all
        (name: !(builtins.hasAttr name config.environment.etc))
        retiredRepoMapNewsyslogEntries;
      laptopNewsyslog = builtins.all
        (name: !(builtins.hasAttr name laptopConfig.environment.etc))
        retiredRepoMapNewsyslogEntries;
    };
    expected = {
      macBookProUserAgents = true;
      macBookProSystemAgents = true;
      laptopUserAgents = true;
      laptopSystemAgents = true;
      macBookProNewsyslog = true;
      laptopNewsyslog = true;
    };
  };

  testRetiredRepoMapShellPathAndActivationEffectsAreAbsent = {
    expr = {
      macBookProInteractivePath = !(lib.hasInfix
        "repo-map_ctrl/bin"
        config.programs.zsh.interactiveShellInit);
      macBookProLoginPath = !(lib.hasInfix
        "repo-map_ctrl/bin"
        config.programs.zsh.loginShellInit);
      laptopInteractivePath = !(lib.hasInfix
        "repo-map_ctrl/bin"
        laptopConfig.programs.zsh.interactiveShellInit);
      laptopLoginPath = !(lib.hasInfix
        "repo-map_ctrl/bin"
        laptopConfig.programs.zsh.loginShellInit);
      macBookProActivation = builtins.all
        (needle: !(lib.hasInfix needle activationText))
        retiredRepoMapActivationNeedles;
      laptopActivation = builtins.all
        (needle: !(lib.hasInfix needle laptopConfig.system.activationScripts.postActivation.text))
        retiredRepoMapActivationNeedles;
    };
    expected = {
      macBookProInteractivePath = true;
      macBookProLoginPath = true;
      laptopInteractivePath = true;
      laptopLoginPath = true;
      macBookProActivation = true;
      laptopActivation = true;
    };
  };

  testDinasNamespaceActivationUsesProjectedPathsAndFailsLoudly = {
    expr = {
      packagedReconciler = lib.hasInfix "/bin/dinas-reconcile" activationText;
      currentRoot = lib.hasInfix "--nbin-root /Users/operator/.local/nbin" activationText;
      developmentRoot = lib.hasInfix "--nbin-dev-root /Users/operator/.local/nbin_dev" activationText;
      aiRoot = lib.hasInfix "--ai-index-root /Users/operator/ai" activationText;
      namespacesJson = lib.hasInfix "--namespaces-json" activationText;
      devNamespacesJson = lib.hasInfix "--dev-namespaces-json" activationText;
      noLegacyTarget = !(lib.hasInfix "--legacy-nbin-target" activationText);
      noLegacyFlakes = !(lib.hasInfix ".flakes/nix-darwin/bin" activationText);
      noSuppressedFailure = !(lib.hasInfix "--dev-namespaces-json '{}' || true" activationText);
    };
    expected = {
      packagedReconciler = true;
      currentRoot = true;
      developmentRoot = true;
      aiRoot = true;
      namespacesJson = true;
      devNamespacesJson = true;
      noLegacyTarget = true;
      noLegacyFlakes = true;
      noSuppressedFailure = true;
    };
  };

  testMacBookProIdentityAndBuilderIds = {
    expr = {
      hostName = config.networking.hostName;
      computerName = config.networking.computerName;
      primaryUser = config.system.primaryUser;
      primaryUserHome = config.users.users.${config.system.primaryUser}.home;
      nixbldUid = config.ids.uids.nixbld;
      nixbldGid = config.ids.gids.nixbld;
    };
    expected = {
      hostName = "example-primary";
      computerName = "example-primary";
      primaryUser = "operator";
      primaryUserHome = "/Users/operator";
      nixbldUid = 350;
      nixbldGid = 350;
    };
  };

  testLaptopIdentityAndBuilderIds = {
    expr = {
      hostName = laptopConfig.networking.hostName;
      computerName = laptopConfig.networking.computerName;
      primaryUser = laptopConfig.system.primaryUser;
      primaryUserHome = laptopConfig.users.users.${laptopConfig.system.primaryUser}.home;
      nixbldUid = laptopConfig.ids.uids.nixbld;
      nixbldGid = laptopConfig.ids.gids.nixbld;
    };
    expected = {
      hostName = "example-workstation";
      computerName = "example-workstation";
      primaryUser = "operator";
      primaryUserHome = "/Users/operator";
      nixbldUid = 350;
      nixbldGid = 350;
    };
  };

  testShellActivationUsesTheTargetUserWriter = {
    expr = hasActivationSnippets [
      "setting up shell configuration..."
      "shell-writer.sh"
      ''/usr/bin/sudo -n -H -u "$target_user" --''
    ];
    expected = true;
  };

  testManagedSystemPackagesIncludeTestToolchain = {
    expr = {
      bats = hasPackage "bats";
      nixUnit = hasPackage "nix-unit";
      pytest = hasPackage "pytest";
      zunit = hasPackage "zunit";
    };
    expected = {
      bats = true;
      nixUnit = true;
      pytest = true;
      zunit = true;
    };
  };

  testManagedPythonEnvironmentIsInstalledFromNixpkgs = {
    expr = {
      packageOutput = builtins.hasAttr "nixDarwinManagedPython" flake.packages.${system};
      macBookProHasPackage = builtins.elem
        flake.packages.${system}.nixDarwinManagedPython
        config.environment.systemPackages;
      laptopHasPackage = builtins.elem
        flake.packages.${system}.nixDarwinManagedPython
        laptopConfig.environment.systemPackages;
      bothHostsIdentical =
        builtins.elem flake.packages.${system}.nixDarwinManagedPython config.environment.systemPackages
        && builtins.elem flake.packages.${system}.nixDarwinManagedPython laptopConfig.environment.systemPackages;
      noExternalPythonInstaller =
        !(lib.hasInfix "pip install" activationText)
        && !(lib.hasInfix "pip3 install" activationText);
    };
    expected = {
      packageOutput = true;
      macBookProHasPackage = true;
      laptopHasPackage = true;
      bothHostsIdentical = true;
      noExternalPythonInstaller = true;
    };
  };

  testManagedRubyEnvironmentIsInstalledFromNixpkgs = {
    expr = {
      packageOutput = builtins.hasAttr "nixDarwinManagedRuby" flake.packages.${system};
      systemPackage = builtins.elem
        flake.packages.${system}.nixDarwinManagedRuby
        config.environment.systemPackages;
      absentFromHomebrew = builtins.all
        (name: !(builtins.elem name brewNames))
        [ "ruby" "pry" "bundler" "rake" "rubygems-update" "minitest" "ruby-lsp" ];
    };
    expected = {
      packageOutput = true;
      systemPackage = true;
      absentFromHomebrew = true;
    };
  };

  testManagedNodeEnvironmentIsInstalledFromNixpkgs = {
    expr =
      let
        hasManagedNode =
          builtins.hasAttr "nixDarwinManagedNode" flake.packages.${system};
      in
      {
        packageOutput = hasManagedNode;
        systemPackage = hasManagedNode
          && builtins.elem
            flake.packages.${system}.nixDarwinManagedNode
            config.environment.systemPackages;
        absentFromHomebrew = builtins.all
          (name: !(builtins.elem name brewNames))
          [ "node" "nodejs" "npm" "angular-cli" "nest-cli" "playwright" ];
      };
    expected = {
      packageOutput = true;
      systemPackage = true;
      absentFromHomebrew = true;
    };
  };

  testStorageOperatorDeclarationsHaveMovedAndSqlCapabilityRemains = {
    expr = {
      cask = builtins.elem "arq" caskNames;
      restoreCli = hasPackage "arq_restore";
      postgresClient = hasPackage "postgresql";
      postgresDevTools = builtins.elem pkgs.postgresql.dev config.environment.systemPackages;
      postgresPgConfig = builtins.elem pkgs.postgresql.pg_config config.environment.systemPackages;
    };
    expected = {
      cask = false;
      restoreCli = false;
      postgresClient = true;
      postgresDevTools = true;
      postgresPgConfig = true;
    };
  };

  testSystemPackagesCoverManagedCliAndDeveloperTools = {
    expr = {
      coreSystem = builtins.all hasPackage [
        "git-gui"
        "gh"
        "fastfetch-profile-wrapper"
        "rtk"
        "repomap-kg"
        "dasel"
        "betterleaks"
        "tmux"
        "tmuxplugin-sensible"
      ];
      developerTools = builtins.all hasPackage [
        "android-tools"
        "corepack-nodejs"
        "liquibase"
        "ngrok"
        "nil"
        "nixd"
        "nixfmt"
        "ripgrep"
        "ShellCheck"
        "shfmt"
      ];
      languageManagers = builtins.all hasPackage [
        "pyenv"
        "uv"
        "curl"
        "unzip"
        "zip"
      ];
      mediaAndNetworkTools = builtins.all hasPackage [
        "nmap"
        "qbittorrent"
      ];
      ytDlpAbsent = !(hasPackage "yt-dlp");
      vmwareFusionTools = builtins.all hasPackage [
        "vmware-fusion-vmcli"
        "vmware-fusion-vmrest"
        "vmware-fusion-vmrun"
        "vmware-fusion-ovftool"
      ];
      tmuxPluginPathLinked = builtins.elem
        "/share/tmux-plugins"
        config.environment.pathsToLink;
    };
    expected = {
      coreSystem = true;
      developerTools = true;
      languageManagers = true;
      mediaAndNetworkTools = true;
      ytDlpAbsent = true;
      vmwareFusionTools = true;
      tmuxPluginPathLinked = true;
    };
  };

  testHomebrewConfigurationCoversManagedAppsAndLanguageManagers = {
    expr = {
      casks = allIn [
        "adobe-acrobat-reader"
        "ente-auth"
        "firefox"
        "keepassxc"
        "kitty"
        "logi-options+"
        "logos"
        "microsoft-teams"
        "orbstack"
        "zoom"
        "libreoffice"
        "onlyoffice"
        "zettlr"
      ] caskNames;
      grandPerspectiveExternal = !(builtins.elem "grandperspective" caskNames);
      brews = allIn [
        "kcov"
        "mas"
        "nvm"
        "rbenv"
        "ruby-build"
      ] brewNames;
      taps = allIn [
      ] tapNames;
      masApps = config.homebrew.masApps;
    };
    expected = {
      casks = true;
      grandPerspectiveExternal = true;
      brews = true;
      taps = true;
      masApps = {
        "Bible Study" = 472790630;
        "Command X" = 6448461551;
        "Pure Paste" = 1611378436;
      };
    };
  };

  testFontsAndSecuritySettingsMatchManagedHostPolicy = {
    expr = {
      fonts = builtins.all hasFontPrefix [
        "JuliaMono-ttf-"
        "jetbrains-mono-"
        "nerd-fonts-fira-code-"
        "nerd-fonts-iosevka-"
        "nerd-fonts-jetbrains-mono-"
        "ubuntu-sans-mono-"
      ];
      firewall = config.networking.applicationFirewall;
      guestLogin = config.system.defaults.loginwindow.GuestEnabled;
      sudoTouchId = {
        enable = config.security.pam.services.sudo_local.enable;
        reattach = config.security.pam.services.sudo_local.reattach;
        touchIdAuth = config.security.pam.services.sudo_local.touchIdAuth;
      };
    };
    expected = {
      fonts = true;
      firewall = {
        allowSigned = true;
        allowSignedApp = true;
        blockAllIncoming = false;
        enable = true;
        enableStealthMode = true;
      };
      guestLogin = false;
      sudoTouchId = {
        enable = true;
        reattach = true;
        touchIdAuth = true;
      };
    };
  };

  testLaunchdServicesExposeManagedScriptsAndIntervals = {
    expr = {
      cloudCapturePresent = config.launchd.agents ? cloud-capture-sync;
      hostStealthMode =
        let service = serviceSubset config.launchd.daemons.host-stealth-mode.serviceConfig;
        in {
          bash = lib.hasSuffix "/bin/bash" (builtins.elemAt service.ProgramArguments 0);
          script = lib.hasSuffix "host-stealth-mode.sh" (builtins.elemAt service.ProgramArguments 1);
          libexec = lib.hasSuffix "host-security" service.EnvironmentVariables.HOST_STEALTH_MODE_LIBEXEC;
          trustedGatewayMacs = service.EnvironmentVariables.TRUSTED_GATEWAY_MACS;
          runAtLoad = service.RunAtLoad;
          startInterval = service.StartInterval;
          standardOutPath = service.StandardOutPath;
          standardErrorPath = service.StandardErrorPath;
        };
      hostEgress =
        let service = serviceSubset config.launchd.daemons.host-egress-pf.serviceConfig;
        in {
          bash = lib.hasSuffix "/bin/bash" (builtins.elemAt service.ProgramArguments 0);
          script = lib.hasSuffix "host-egress-pf.sh" (builtins.elemAt service.ProgramArguments 1);
          rules = lib.hasSuffix "host-egress.pf.conf" service.EnvironmentVariables.HOST_EGRESS_RULES;
          runAtLoad = service.RunAtLoad;
          startInterval = service.StartInterval;
          standardOutPath = service.StandardOutPath;
          standardErrorPath = service.StandardErrorPath;
        };
    };
    expected = {
      cloudCapturePresent = false;
      hostStealthMode = {
        bash = true;
        script = true;
        libexec = true;
        trustedGatewayMacs = "";
        runAtLoad = true;
        startInterval = 60;
        standardOutPath = "/var/log/host-stealth-mode.log";
        standardErrorPath = "/var/log/host-stealth-mode.log";
      };
      hostEgress = {
        bash = true;
        script = true;
        rules = true;
        runAtLoad = true;
        startInterval = 300;
        standardOutPath = "/var/log/host-egress-pf.log";
        standardErrorPath = "/var/log/host-egress-pf.log";
      };
    };
  };

  testKittyLaunchersKeepDistinctShellEntrypoints = {
    expr =
      let writerText = builtins.readFile (repoRoot + "/modules/kitty-app-writer.sh"); in
      builtins.all (needle: lib.hasInfix needle (activationText + writerText)) [
        ''kitty_zsh_bundle_id="com.operator.kitty.zsh"''
        ''kitty_bash_bundle_id="com.operator.kitty.bash"''
        ''create_kitty_launcher "Kitty Zsh" "$zsh_bundle_id" "kitty-zsh" "/bin/zsh -l" "Z"''
        ''create_kitty_launcher "Kitty Bash" "$bash_bundle_id" "kitty-bash" "/usr/bin/login -fp $target_user /run/current-system/sw/bin/bash" "B"''
      ];
    expected = true;
  };

  testActivationScriptsCoverManagedUserConfiguration = {
    expr = builtins.filter (snippet: !(lib.hasInfix snippet activationText)) [
      "setting up shell configuration..."
      "shell-writer.sh"
      "reconciling DINAS command namespaces and AI index..."
      "setting up Terminal profile..."
      "columnCount' -integer 90"
      "setting up kitty config..."
      "kitty-app-writer.sh"
      ''kitty_bash_bundle_id="com.operator.kitty.bash"''
      "setting up fastfetch config..."
      "config-kitty.jsonc"
      "setting up Codex state paths..."
      "target_user=operator"
      "user_home=/Users/operator"
      "/usr/bin/sudo -n -H -u \"$target_user\" --"
      "/bin/sh /nix/store/"
      "-codex-state-writer.sh"
      "agent_central_root=/Users/operator/.local/nix-darwin/agent-central_dinas"
      "\"$target_user\" \"$user_home\" \"$agent_central_root\" || exit 1"
      "setting up Brave YouTube app..."
      "com.operator.brave-youtube"
      "setting up video session power controls..."
      "video_session_pmset"
      "setting up Oh My Zsh..."
      "oh-my-zsh-writer.sh"
      "setting up nvm..."
      "NVM_DIR"
      "setting up pyenv..."
      "PYENV_ROOT"
      "setting up rbenv..."
      "RBENV_ROOT"
      "setting up SDKMAN..."
      "SDKMAN_DIR"
      "setting up browser policies..."
      "AutofillCreditCardEnabled"
    ];
    expected = [ ];
  };

  testCoreOnlyArqActivationAndServicesAreAbsent = {
    expr = builtins.all (hostConfig:
      !(lib.hasInfix "setting up Arq cloud capture paths..."
        hostConfig.system.activationScripts.postActivation.text)
      && !(lib.hasInfix "arq-writer.sh"
        hostConfig.system.activationScripts.postActivation.text)
      && !(hostConfig.launchd.agents ? cloud-capture-sync)
      && !(hostConfig.launchd.user.agents ? cloud-capture-sync)
      && !(hostConfig.launchd.daemons ? cloud-capture-sync)
      && !(builtins.elem "arq" (map (cask: cask.name) hostConfig.homebrew.casks))
    ) [ config laptopConfig ];
    expected = true;
  };

  testBothHostsRenderCanonicalCodexInvocation = {
    expr = builtins.all (hostConfig:
      lib.hasInfix ''agent_central_root=/Users/operator/.local/nix-darwin/agent-central_dinas''
        hostConfig.system.activationScripts.postActivation.text
      && lib.hasInfix ''"$target_user" "$user_home" "$agent_central_root" || exit 1''
        hostConfig.system.activationScripts.postActivation.text
    ) [ config laptopConfig ];
    expected = true;
  };

  testCodexStateActivationHasOneBoundedPrivilegeTransition =
    let
      moduleText = moduleSource "codex-state";
      writerText = builtins.readFile (repoRoot + "/modules/codex-state-writer.sh");
      occurrenceCount = marker: text: builtins.length (lib.splitString marker text) - 1;
    in
    {
      expr = {
        writerCount = occurrenceCount "codex-state-writer.sh" activationText;
        moduleBoundary = builtins.all (needle: lib.hasInfix needle moduleText) [
          "primaryUser = config.system.primaryUser;"
          ''userHome = config.users.users.''${primaryUser}.home;''
          ''/usr/bin/sudo -n -H -u "$target_user" --''
          ''/bin/sh ''${./codex-state-writer.sh}''
          ''"$target_user" "$user_home" "$agent_central_root" || exit 1''
        ];
        moduleHasNoUserPathMutation = builtins.all
          (needle: !(lib.hasInfix needle moduleText))
          [ "chown" "chgrp" "find " "install " "ln " "mkdir " "mv " "rm " "rmdir " ];
        writerIdentityBoundary = builtins.all (needle: lib.hasInfix needle writerText) [
          "PATH=/usr/bin:/bin"
          ''effective_uid=$(/usr/bin/id -u)''
          ''if [ "$effective_uid" -eq 0 ]; then''
          ''if [ "$(/usr/bin/id -un)" != "$target_user" ]; then''
          ''require_owned_directory "$user_home" "resolved user home"''
        ];
        writerHasNoPrivilegeRepair = builtins.all
          (needle: !(lib.hasInfix needle writerText))
          [ "chown" "chgrp" "sudo" "/Users/" ];
      };
      expected = {
        writerCount = 1;
        moduleBoundary = true;
        moduleHasNoUserPathMutation = true;
        writerIdentityBoundary = true;
        writerHasNoPrivilegeRepair = true;
      };
    };

  testSourceOnlySecurityAndSshContractsStayHardened = {
    expr = {
      ssh = moduleHasSnippets "ssh" [
        "PubkeyAuthentication yes"
        "AuthenticationMethods publickey"
        "PasswordAuthentication no"
        "KbdInteractiveAuthentication no"
        "PermitRootLogin no"
      ];
      browsers = moduleHasSnippets "browsers" [
        "keepassxc-chrome-native-messaging.json"
        "keepassxc-firefox-native-messaging.json"
        "AutofillAddressEnabled"
      ];
      braveYoutube = moduleHasSnippets "brave-youtube" [
        "braveYoutubeLauncherTemplate"
        "braveYoutubeGenerateThemeAssets"
        "com.operator.brave-youtube"
      ];
      nixpkgsPolicy = moduleHasSnippets "nixpkgs-policy" [
        "google-chrome"
        "softnet"
        "tart"
      ];
    };
    expected = {
      ssh = true;
      browsers = true;
      braveYoutube = true;
      nixpkgsPolicy = true;
    };
  };

  testProjectedNbinRootShellConfiguration = {
    expr = {
      projectedNbinRoot = shellConfig.dinasProjectedNbinRoot;
      fragmentHasProjectedAssignment = lib.hasInfix
        "_dinas_projected_nbin_root=${lib.escapeShellArg "/Users/operator/.local/nbin"}"
        shellConfig.dinasPathsFragment;
      activationUsesBuiltShellConfig = lib.hasInfix
        (builtins.unsafeDiscardStringContext "${shellConfig}")
        (builtins.unsafeDiscardStringContext activationText);
    };
    expected = {
      projectedNbinRoot = "/Users/operator/.local/nbin";
      fragmentHasProjectedAssignment = true;
      activationUsesBuiltShellConfig = true;
    };
  };

  testDinasDeploymentEffectsMetadata = {
    expr = {
      schema = config.dinas.deploymentEffects.schema;
      preflightCount = builtins.length config.dinas.deploymentEffects.preflightParticipants;
      firstPreflightName = (builtins.head config.dinas.deploymentEffects.preflightParticipants).name;
      firstPreflightReadOnly = (builtins.head config.dinas.deploymentEffects.preflightParticipants).readOnly;
      firstPreflightHasCheck = builtins.elem "--check" (builtins.head config.dinas.deploymentEffects.preflightParticipants).arguments;
      firstPreflightNoLegacy = !(builtins.elem "--legacy-nbin-target" (builtins.head config.dinas.deploymentEffects.preflightParticipants).arguments);
      firstPreflightNoFlakes = builtins.all (arg: !(lib.hasInfix ".flakes/nix-darwin/bin" arg)) (builtins.head config.dinas.deploymentEffects.preflightParticipants).arguments;
      firstPreflightHasCanonicalArgs = builtins.all (arg: builtins.elem arg (builtins.head config.dinas.deploymentEffects.preflightParticipants).arguments) [
        "--nbin-root"
        "--nbin-dev-root"
        "--ai-index-root"
        "--namespaces-json"
        "--dev-namespaces-json"
      ];
      preSwitchCount = builtins.length config.dinas.deploymentEffects.preSwitchTransactions;
      postSwitchCount = builtins.length config.dinas.deploymentEffects.postSwitchReconciliation;
    };
    expected = {
      schema = "nix-darwin-deployment-effects-v1";
      preflightCount = 1;
      firstPreflightName = "dinas-reconcile";
      firstPreflightReadOnly = true;
      firstPreflightHasCheck = true;
      firstPreflightNoLegacy = true;
      firstPreflightNoFlakes = true;
      firstPreflightHasCanonicalArgs = true;
      preSwitchCount = 0;
      postSwitchCount = 0;
    };
  };

  testGatewayHintsAreSiteLocalAndEmptyByDefault = {
    expr = let
      explicit = flake.lib.mkDarwinConfiguration {
        hostName = "example-primary";
        extraModules = [{ dinas.hostSecurity.trustedGatewayMacs = [ "02:00:00:00:00:01" ]; }];
      };
      invalid = flake.lib.mkDarwinConfiguration {
        hostName = "example-primary";
        extraModules = [{ dinas.hostSecurity.trustedGatewayMacs = [ "not-a-mac" ]; }];
      };
    in {
      emptyDefault = config.dinas.hostSecurity.trustedGatewayMacs == [ ];
      initialStealth = config.networking.applicationFirewall.enableStealthMode;
      explicitHint = explicit.config.launchd.daemons.host-stealth-mode.serviceConfig.EnvironmentVariables.TRUSTED_GATEWAY_MACS;
      invalidRejected = !(builtins.tryEval (builtins.deepSeq invalid.config.dinas.hostSecurity.trustedGatewayMacs true)).success;
    };
    expected = {
      emptyDefault = true;
      initialStealth = true;
      explicitHint = "02:00:00:00:00:01";
      invalidRejected = true;
    };
  };

}
