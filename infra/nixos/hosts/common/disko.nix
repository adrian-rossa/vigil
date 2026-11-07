{ diskDevice, ... }:
let
  # udevadm settle can return before udev recreates the partition links after the last table re-read
  waitForPartitionLink = ''udevadm wait --settle --timeout 60 "$device"'';
in
{
  disko.devices.disk.main = {
    type = "disk";
    device = diskDevice;
    content = {
      type = "gpt";
      partitions = {
        bios = {
          size = "1M";
          type = "EF02";
        };
        ESP = {
          size = "512M";
          type = "EF00";
          content = {
            type = "filesystem";
            format = "vfat";
            mountpoint = "/boot";
            preCreateHook = waitForPartitionLink;
          };
        };
        root = {
          size = "100%";
          content = {
            type = "filesystem";
            format = "ext4";
            mountpoint = "/";
            preCreateHook = waitForPartitionLink;
          };
        };
      };
    };
  };
}
