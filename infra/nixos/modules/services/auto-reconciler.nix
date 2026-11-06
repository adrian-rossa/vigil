{ config, pkgs, lib, ... }:
let
  cfg = config.services.vigilAutoReconciler;
  etcDir = "/etc/vigil";
in
{
  options.services.vigilAutoReconciler.enable = lib.mkEnableOption "vigil NixOS auto-reconciler";

  config = lib.mkIf cfg.enable {
    systemd.services.vigil-auto-reconcile = {
      description = "Vigil NixOS auto-reconciler";
      path = [ pkgs.git pkgs.nixos-rebuild pkgs.util-linux ];
      restartIfChanged = false;
      unitConfig = {
        ConditionPathExists = "${etcDir}/branch";
        X-StopOnRemoval = false;
      };
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = false;
      };
      environment = {
        VIGIL_ETC_DIR = etcDir;
        VIGIL_CONFIG_DIR = "/opt/nixos-config";
        VIGIL_STATE_FILE = "/var/lib/vigil-reconciler/last-sha";
        VIGIL_REBUILD_LOCK = "/var/lock/vigil-nixos-rebuild";
      };
      script = builtins.readFile ./auto-reconcile.sh;
    };

    systemd.timers.vigil-auto-reconcile = {
      description = "Timer for vigil NixOS auto-reconciler";
      wantedBy = [ "multi-user.target" ];
      timerConfig = {
        OnBootSec = "300s";
        OnUnitActiveSec = "300s";
        Unit = "vigil-auto-reconcile.service";
      };
    };
  };
}
