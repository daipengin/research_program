# CS starvation check

| run_index | skip_events_last_50_cycles | active_devices | top_device | top_device_share | classification |
| --- | --- | --- | --- | --- | --- |
| 0 | 61 | 3 | 20 | 0.639344262295082 | fixed_starvation |
| 1 | 64 | 3 | 3 | 0.4375 | rotating_or_distributed |
| 2 | 107 | 5 | 7 | 0.4485981308411215 | rotating_or_distributed |

A fixed starvation pattern is defined here as one device accounting for at least 50% of final-50-cycle skip events.
