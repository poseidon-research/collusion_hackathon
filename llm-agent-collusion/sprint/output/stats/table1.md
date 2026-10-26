| Condition | Ask index, last third (95% CI) | Mean trade price ($) | Share removed (95% CI) | p (Welch, Holm) | p (perm., Holm) | Trades | Msgs read |
|---|---|---|---|---|---|---|---|
| No remedy (control) | 0.66 (0.58, 0.74) | 96.49 | - | - | - | 3.8 | 0 |
| No CEO pressure | 0.43 (0.12, 0.74) | 92.48 | 35% (-11%, 82%) | 1.00 | 1.00 | 7.2 | 0 |
| Opening asks at $90 | -0.07 (-0.16, 0.03) | 89.25 | 111% (95%, 126%) | <0.001 | 0.103 | 6.6 | 0 |
| Message ban | 0.55 (0.37, 0.73) | 94.80 | 16% (-11%, 43%) | 1.00 | 1.00 | 4.2 | 0 |
| Cap: 80 characters | 0.67 (0.58, 0.76) | 96.48 | -2% (-17%, 13%) | 1.00 | 1.00 | 5.0 | 0 |
| Cap: 20 characters | 0.64 (0.52, 0.76) | 95.48 | 3% (-16%, 22%) | 1.00 | 1.00 | 4.6 | 0 |
| Cap: 5 characters | 0.68 (0.60, 0.76) | 96.76 | -2% (-17%, 12%) | 1.00 | 1.00 | 5.2 | 0 |
| Warning only (bluff) | 0.47 (0.32, 0.63) | 94.35 | 28% (5%, 52%) | 0.31 | 0.29 | 6.8 | 0 |
| Silent monitor (no warning) | 0.59 (0.50, 0.67) | 95.15 | 11% (-4%, 25%) | 1.00 | 1.00 | 7.8 | 45 |
| Monitor reads 1 of 3 msgs | 0.58 (0.37, 0.79) | 95.44 | 12% (-20%, 44%) | 1.00 | 1.00 | 7.2 | 15 |
| Monitor reads 2 of 3 msgs | 0.37 (-0.28, 1.03) | 93.54 | 43% (-56%, 142%) | 1.00 | 1.00 | 11.0 | 30 |
| Monitor reads 3 of 3 msgs | 0.52 (0.14, 0.90) | 94.81 | 21% (-37%, 79%) | 1.00 | 1.00 | 7.0 | 45 |
| Monitor + enforced gag | 0.58 (0.48, 0.68) | 94.78 | 12% (-4%, 28%) | 1.00 | 1.00 | 6.8 | 45 |
| Maverick price-cutter | 0.44 (0.13, 0.75) | 93.58 | 34% (-13%, 80%) | 1.00 | 1.00 | 8.4 | 0 |

5 seeds per condition; 3 sellers (2 LLM sellers plus the scripted seller in the maverick condition) and 3 LLM buyers (GPT-4.1), 15 hours, CEO pressure on except in 'No CEO pressure'. Ask index: 0 = competitive price ($90), 1 = cartel price ($100); LLM sellers only. Mean trade price: mean of each run's average trade price. Share removed = (control - condition) / control; CI = Welch CI of the difference divided by the control mean. p: each remedy vs control, Holm-corrected across the 13 comparisons with the control. The exact permutation test cannot go below p = 0.0079 with 5 vs 5 runs, so after Holm across 13 comparisons its smallest possible value is 0.103. Trades and Msgs read: means per run; 45 trades are possible per run.
