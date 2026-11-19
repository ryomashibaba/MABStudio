# MAB V11 architecture evidence

Studio's pinned B143 validates and compiles geometric SOURCE primitives; it does not model room adjacency, door/window connectivity, stair access, or architectural merit. A valid SOURCE can therefore describe an unusable building.

In the saved mountain weather station, the original entrance cut at `[2,4,3]` with size `[2,4,2]` missed the occupied shell beginning at `[4,4,4]`; the deck was an isolated vertical box. The SOURCE passed B143, but there was no usable door, window, or connection between levels. One real Qwen3 revision retained the foundation, observation room, dome, and lightning rod while adding a connected entry, window, mezzanine, and stairs within the 18-element budget. It passed fixed B143 and was visually compared in VTK before approval.

Two new Qwen3 runs from the normal Windows GUI exposed different limits. The “small riverside library” passed B143 at 11,664 cells, but 32×24×32 bounds, a full glass slab roof, cuts placed away from the shell, and a bookshelf-sized solid block left no usable entrance or reading space. The “two-storey fire station” was rejected before saving because its antenna extended to Y=24 while bounds.height was 24 (valid coordinates end at 23). One library delta edit was safely discarded because returned edit IDs did not match the original SOURCE. These examples show that geometric validity and valid delta JSON still do not prove functional architecture.

V11 should own semantic building topology and evaluation: occupied-room connectivity to exterior doors, stair-to-floor links, window openings aligned to actual walls, and preservation checks for named program/features. Studio should keep emitting portable SOURCE for the pinned engine rather than growing a second architectural engine. This evidence concerns functional coherence; the approved result remains voxel-based and has not been judged as finished architectural art.

Evidence boundary: fixed B143 commit `b0295c2f5012877f57ae218ae791634794de7b52`; approved revision `d138e48a647748c3bf5212861e2b591c`; world writes `0`; BuildPlan operations `0`; construction readiness `NOT_PREPARED`.

## V11-to-Studio streaming contract (read-only)

To deliver a Studio-compatible proposal without replacing Studio binaries, V11
may publish `V11_READY_SOURCE.json` (a full B143 DESIGN JSON, or a
`TRUE_B143_PRODUCT.json`-compatible safe wrapper) to the user's synced
`MABStudio_Work_Handoff_20261118` folder or local Studio `v11-inbox`. The V11
team owns producing and validating the architectural plan. Studio independently
rechecks the published design through its frozen, SHA-pinned B143; it does not
flatten `repeat`, `staircase`, `beam`, `torus`, or change the engine. New arrivals
are previewed only after the user's review action and always remain unapproved
until explicitly saved. Engine upgrades need a reviewed Studio release.
