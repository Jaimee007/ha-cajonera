# ha-cajonera

Home Assistant integration for the modular drawer cabinet ("cajonera"): a search box, a drawer
grid, drawer contents with a withdraw action and a low-stock list, backed by
[InvenTree](https://inventree.org). Until the cabinet controller firmware exists, a drawer
simulator inside the integration plays its part. The hardware, firmware and inventory design live in
[Modular_Drawers](https://github.com/Jaimee007/Modular_Drawers), see its
`docs/09_gestion_inventario.md`.

## Installation (HACS)

1. In HACS open the three-dot menu, choose **Custom repositories** and add
   `https://github.com/Jaimee007/ha-cajonera` with category **Integration**.
2. Install **Cajonera** and restart Home Assistant.

## Configuration

Add the block below to `configuration.yaml` and the token to `secrets.yaml`
(`inventree_token: "<token of the HA user in InvenTree>"`). Use the LOCAL address of InvenTree when
possible: Home Assistant does not need to go through the tunnel.

```yaml
cajonera:
  inventree_url: http://192.168.1.10:8000
  inventree_token: !secret inventree_token
  simulated_drawers:            # remove once the real controller exists
    - {address: 0,  x: 0, y: 0}            # A1, the base
    - {address: 3,  x: 1, y: 0}            # A2
    - {address: 4,  x: 2, y: 0}            # A3
    - {address: 17, x: 0, y: 1, width: 2}  # B1, double drawer (B1-B2)
    - {address: 21, x: 2, y: 1}            # B3
```

## The card

The card has screens for search, the part sheet (add, withdraw, update, move and edit a part's
stock and details), and creating parts and categories. The integration serves the card itself, so
no dashboard resource is needed. Add it to a dashboard:

```yaml
type: custom:cajonera-card
```

### Migrating from the manual zip

If you installed the card by hand before, remove the old files and resource, otherwise the card is
loaded twice:

- delete `/config/www/cajonera-card.js` and `/config/www/cajonera-layout.js`;
- delete the `/local/cajonera-card.js` resource from the dashboard resources.

## Services

All services live in the `cajonera` domain; fields and descriptions are in
[`services.yaml`](custom_components/cajonera/services.yaml).

| Service | Purpose |
|---|---|
| `cajonera.inventario_buscar` | Search the inventory and light the drawer of a part |
| `cajonera.inventario_retirar` | Withdraw stock from a part, by text (voice), stock item or part id (`pieza_pk`) |
| `cajonera.contenido_cajon` | List the contents of a drawer |
| `cajonera.identificar` | Make a drawer blink |
| `cajonera.sincronizar_ubicaciones` | Create and rename InvenTree locations to match the drawers |
| `cajonera.redescubrir` | Map the cabinet again and sync its locations |
| `cajonera.simular_apertura` | Open or close a simulated drawer (simulated drawers only) |
| `cajonera.sugerir` | Suggest parts while typing, without lighting any drawer |
| `cajonera.iluminar_busqueda` | Light the drawers of the suggestions the card shows while typing (an empty list puts them out) |
| `cajonera.pieza` | Return a part's sheet, light its drawers and remember it as the last part |
| `cajonera.inventario_anadir` | Add units of a part to a drawer |
| `cajonera.inventario_actualizar` | Set the counted quantity of a part in a drawer |
| `cajonera.inventario_mover` | Move a part from one drawer to another |
| `cajonera.categorias` | Return the InvenTree part categories |
| `cajonera.categoria_crear` | Create a part category, or return the existing one with that name |
| `cajonera.sugerir_cajon` | Suggest the drawer for a new part of a category (it lights nothing) |
| `cajonera.guardar_aqui` | Light in magenta the drawer a part is going to be stored in |
| `cajonera.guardar_apagar` | Put out the magenta store-here light (a search in progress is left alone) |
| `cajonera.pieza_crear` | Create a part with its initial quantity in a drawer |
| `cajonera.pieza_editar` | Change only the given details of a part |

## Development

```
pip install -r requirements-dev.txt
python -m pytest -q
node --test tests/frontend/cajonera-layout.test.mjs
```
