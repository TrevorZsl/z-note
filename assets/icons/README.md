# Icons

Source: Lucide (https://github.com/lucide-icons/lucide), as showcased by Morphicons (https://www.morphicons.com/).

Original SVGs and license are included. PNGs are rendered at 64 px from the SVGs using Sharp, then recolored to #4B5563 (normal) or #1F2328 (active pin / arrow-down-to-line). Tkinter displays them at 22 px, or 26 px for the add-todo plus.

Mapping: pin = toggle always-on-top; plus = new note / add todo; trash = delete note; minus = hide note.

columns-2 = toggle single/double column layout; PNGs are rendered from the official SVG geometry with Pillow at 4x (same pipeline as the arrows).

arrow-down-to-line = toggle always-on-bottom; pin and arrow-down-to-line are mutually exclusive. Arrow PNGs are rendered from the straight SVG paths with Pillow at 4x resolution and downsampled to 64 px.

The dock disclosure indicator uses the filled [Lucide Play path](https://lucide.dev/icons/play) in the normal icon color (#4B5563). The down PNG rotates the right indicator by 90° clockwise.
