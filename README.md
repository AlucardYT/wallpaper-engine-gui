# Wallpaper Engine GUI

Front-end GTK4/libadwaita para o Wallpaper Engine no Linux, integrado ao Caelestia.
Não roda o `linux-wallpaperengine` sozinho: aplica o wallpaper pelo IPC do Caelestia
(`qs -c caelestia ipc call wallpaper set …/project.json`), então as cores e os hooks continuam funcionando.

- **Biblioteca:** wallpapers baixados da Workshop (Steam), com ordenação, volume e detalhes.
- **Oficina:** navegar e se inscrever na Workshop direto no app (login da Steam pelo WebKit).

## Uso

```sh
./instalar.sh            # launcher em ~/.local/bin + atalho no menu
wallpaper-engine-gui     # abre o app
wallpaper-engine-gui --oficina   # abre direto na aba Oficina
```

Requer: Python 3, PyGObject, GTK4, libadwaita, WebKitGTK 6, Pillow, e o Caelestia shell.
