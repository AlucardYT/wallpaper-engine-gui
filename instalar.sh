#!/bin/sh
# Põe o launcher em ~/.local/bin e o atalho no menu de apps.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/applications
ln -sf "$DIR/wallpaper-engine-gui" ~/.local/bin/wallpaper-engine-gui
sed "s|^Exec=.*|Exec=$DIR/wallpaper-engine-gui|" "$DIR/io.github.alucky.WallpaperEngine.desktop" \
    > ~/.local/share/applications/io.github.alucky.WallpaperEngine.desktop
echo "Instalado. Abra pelo menu ou com: wallpaper-engine-gui"
