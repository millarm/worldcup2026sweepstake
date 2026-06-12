# Nix environment for Replit. Python is provided by the python-3.11 module in
# .replit; dependencies are installed from requirements.txt by the run command.
{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
  ];
}
