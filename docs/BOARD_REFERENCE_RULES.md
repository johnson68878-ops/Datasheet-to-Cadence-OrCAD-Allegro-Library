# Board-derived reference rules / 板级参考规则

Published with owner authorization. Company names, board identifiers and reference designators are removed; original CAD files are not distributed. Implementation: `src/datasheet_to_allegro/reference_defaults.py`.

These are empirical review fallbacks, not manufacturer recommendations, universal IPC prescriptions or production approval. They cannot establish missing pin count, view, numbering, body size or ball occupancy.

| Rule | Applicable condition | Value / behavior |
|---|---|---|
| Array copper land | BGA/WLCSP, both resolved pitches equal 0.35 mm within 0.00001; no supplied land rule | 0.22 x 0.22 mm land dimensions |
| Array solder mask | Same pitch/family condition; no supplied mask rule | 0.04 mm expansion |
| Array paste | Same pitch/family condition; no supplied paste rule | Scale 1.0 |
| Exposed-pad paste subdivision | Positive EP dimensions; QFN/DFN/SON/TSSOP/HTSSOP/QFP; paste status missing and value absent | Target 62% coverage; 0.25 mm inter-window gap; search 1..64 rows and columns |
| EP search limits | Feasible subdivision candidates | Maximum window edge 1.01 mm, minimum edge 0.15 mm; minimize window count, then aspect-ratio imbalance |

Explicit original land/mask/paste values and manufacturer-recommended status take precedence. EP subdivision returns no fallback when the constraints cannot be satisfied. Successful fallback is marked reference_design and draft, with BOARD_REFERENCE_DEFAULT_APPLIED warning. Review the code for exact precedence and validation behavior; these profiles do not bypass the generation gates.

The helper default coverage/gap parameters and pitch-specific array profile are engineering assumptions derived from reference-board review. They need independent fabrication, assembly and source-component assessment before reuse. A profile ID or a matching pitch alone does not establish compatibility with every ball diameter or package variant.

## Chinese summary

已公开原有板级参考缺省算法并移除公司/项目识别。双轴0.35mm的BGA/WLCSP可在缺项时采用0.22mm铜盘、0.04mm阻焊扩展和1.0钢网比例；EP采用目标62%覆盖率与0.25mm间隙进行自适应分窗。它们属于需要Layout/SMT复核的经验规则，不是原厂推荐值，不证明原图解析正确，也不能补猜脚位或封装尺寸。
