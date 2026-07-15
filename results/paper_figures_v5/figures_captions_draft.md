# Figure captions (draft)

## fig_scatter_fluctuation_vs_per

Each point is one function-N-K condition. Small steady-state phase-gap fluctuation does not by itself imply a low packet error rate; the two annotated regions show the two principal counterexamples.

## fig_convergence_rate_vs_ttu_by_k

For N=50, the intended min-gap convergence rate follows the TTU reach rate more closely than the mean- or maximum-deviation criteria. Rates are computed across 1000 independent runs per condition.

## fig_convergence_rate_vs_ttu_by_k_all_n

Appendix view of the four reach-rate criteria over all device counts. The intended min-gap criterion is the collision-free design criterion used in the main text.

## fig_steady_fluctuation_vs_k

Linear fits quantify the local K-dependence of steady maximum phase-gap deviation over the retained high-reach-rate region. Dotted horizontal lines are the N-specific tolerance epsilon_tol; pale isolated markers are conditions with intended min-gap convergence below 50% and are excluded from fitting.

## fig_design_map_n_vs_k

Shaded envelopes show the K ranges with intended min-gap convergence rate at least 95%; points show the PER-optimal K. The overlapping envelopes provide a compact density-dependent design map.

## fig_convergence_speed_two_metrics

For N=50, dashed curves give TTU timing and solid curves give intended min-gap convergence timing. Red markers identify the fastest intended-min-gap point inside each function's at-least-95% reach-rate band.

## fig_per_vs_k

Median PER as a function of K. Red markers show the minimum PER for each device count; the two functions exhibit distinct high-K collapse behavior.

## fig_criterion_random_baseline

Uniform random initial phases were sampled without replacement on the 1-ms grid (10,000 trials per N; seed 20260716). Red segments are the corresponding one-cycle thresholds, showing why an averaged deviation can be permissive even in random configurations.

## fig_transient_demo_min_gap

Representative N=50 trajectories were selected by the run whose intended min-gap convergence cycle is closest to the condition median. The shaded interval is the first qualifying 10-cycle window, and the dashed line is the collision-free gap threshold.

## fig_n5_demo_uneven_but_lossless

A lossless N=5 Kuramoto-based realization (K=10) retains visibly uneven transmission phases. The cumulative PER remains zero, illustrating that ideal equal spacing is not necessary for collision-free operation.

## fig_cs_starvation_demo

In one non-converged Kuramoto-based N=50, K=70 realization, CS skips persistently concentrate on one device over the final 50 cycles. The inset compares that device's intended skip times with the blocking transmission occupancy intervals.
