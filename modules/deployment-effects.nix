{ config, lib, ... }:

let
  cfg = config.dinas.deploymentEffects;

  declarationSubmodule = defaultReadOnly: lib.types.submodule ({ name, ... }: {
    options = {
      name = lib.mkOption {
        type = lib.types.str;
        default = name;
        description = "Unique identifier for this deployment effect";
      };
      owner = lib.mkOption {
        type = lib.types.str;
        description = "Module or subsystem owner declaring this effect";
      };
      kind = lib.mkOption {
        type = lib.types.str;
        default = "command";
        description = "Type or kind of effect declaration";
      };
      protocol = lib.mkOption {
        type = lib.types.str;
        default = "command";
        description = "Invocation protocol";
      };
      executable = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Nix store path to executable";
      };
      arguments = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Argument vector";
      };
      timeout = lib.mkOption {
        type = lib.types.nullOr lib.types.int;
        default = 60;
        description = "Timeout in seconds";
      };
      readOnly = lib.mkOption {
        type = lib.types.bool;
        default = defaultReadOnly;
        description = "Whether effect is strictly read-only";
      };
      classification = lib.mkOption {
        type = lib.types.enum [ "read-only" "mutation" ];
        default = if defaultReadOnly then "read-only" else "mutation";
        description = "Read-only or mutation classification";
      };
      target = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Target participant name for rollback declarations";
      };
    };
  });

  allDeclarations =
    cfg.preflightParticipants
    ++ cfg.preSwitchTransactions
    ++ cfg.postSwitchReconciliation
    ++ cfg.externalMutableStateProbes
    ++ cfg.participantLocalRollback;

  names = builtins.map (d: d.name) allDeclarations;
  namesUnique = builtins.length names == builtins.length (lib.unique names);

  validStorePaths = builtins.all
    (d: d.executable == null || d.executable == "" || lib.hasPrefix "/nix/store/" d.executable)
    allDeclarations;

  consistentClassifications = builtins.all
    (d: (d.readOnly && d.classification == "read-only") || (!d.readOnly && d.classification == "mutation"))
    allDeclarations;

  participantNames = builtins.map (d: d.name) (cfg.preflightParticipants ++ cfg.preSwitchTransactions);
  validRollbacks = builtins.all
    (r: r.target == null || builtins.elem r.target participantNames)
    cfg.participantLocalRollback;
in
{
  options.dinas.deploymentEffects = {
    schema = lib.mkOption {
      type = lib.types.str;
      default = "nix-darwin-deployment-effects-v1";
      readOnly = true;
      description = "Schema identifier";
    };
    preflightParticipants = lib.mkOption {
      type = lib.types.listOf (declarationSubmodule true);
      default = [ ];
      description = "Read-only deployment preflight participants";
    };
    preSwitchTransactions = lib.mkOption {
      type = lib.types.listOf (declarationSubmodule false);
      default = [ ];
      description = "Pre-switch mutating transactions";
    };
    postSwitchReconciliation = lib.mkOption {
      type = lib.types.listOf (declarationSubmodule false);
      default = [ ];
      description = "Post-switch reconciliation effects";
    };
    externalMutableStateProbes = lib.mkOption {
      type = lib.types.listOf (declarationSubmodule true);
      default = [ ];
      description = "External mutable state probes";
    };
    participantLocalRollback = lib.mkOption {
      type = lib.types.listOf (declarationSubmodule false);
      default = [ ];
      description = "Participant-local rollback declarations";
    };
  };

  config = {
    assertions = [
      {
        assertion = namesUnique;
        message = "dinas.deploymentEffects declarations must have unique names across all categories";
      }
      {
        assertion = validStorePaths;
        message = "dinas.deploymentEffects executables must be Nix store paths starting with /nix/store/";
      }
      {
        assertion = consistentClassifications;
        message = "dinas.deploymentEffects readOnly and classification must agree";
      }
      {
        assertion = validRollbacks;
        message = "dinas.deploymentEffects participantLocalRollback must reference an existing participant";
      }
    ];
  };
}
