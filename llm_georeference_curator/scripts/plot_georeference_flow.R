# One renderer for automated output and RStudio; all counts must be real inputs.
# RStudio: source this file, then plot_georeference_flow(counts, "flow.pdf").
# counts: source plus input/evidence/accepted, or collected/unique/evidence/accepted.
script_file <- if (sys.nframe() > 0L && !is.null(sys.frame(1)$ofile)) {
  sys.frame(1)$ofile
} else {
  sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
}
if (length(script_file) == 1L) {
  local_library <- file.path(dirname(script_file), "..", ".Rlibrary")
  if (dir.exists(local_library)) .libPaths(c(normalizePath(local_library), .libPaths()))
}
suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(ggalluvial))

plot_georeference_flow <- function(counts, output) {
  sources <- c("GBIF", "CVH", "Other")
  stopifnot(is.data.frame(counts), !anyDuplicated(names(counts)),
            identical(names(counts), c("source", "input", "evidence", "accepted")) ||
            identical(names(counts), c("source", "collected", "unique", "evidence", "accepted")))
  stopifnot(nrow(counts) == 3L, setequal(counts$source, sources))
  counts <- counts[match(sources, counts$source), , drop = FALSE]
  values <- as.matrix(counts[-1])
  stopifnot(is.numeric(values), all(is.finite(values)), all(values >= 0),
            all(values == floor(values)))
  for (i in seq_len(ncol(values) - 1L)) stopifnot(all(values[, i] >= values[, i + 1L]))
  dir.create(dirname(output), recursive = TRUE, showWarnings = FALSE)
  # Source identity is encoded with the same gray throughout, including removals.
  # Source order is fixed; within each source the earliest exits are at the bottom.
  colours <- c(GBIF = "#1A1A1A", CVH = "#737373", Other = "#BFBFBF")
  stages <- setdiff(names(counts), "source")
  x_stage <- seq(0.8, 6.2, length.out = length(stages))
  top <- max(sum(counts[[stages[[1]]]]), 1)
  label_gap <- 0.065 * top
  width <- 0.034
  nodes <- data.frame()
  for (stage in seq_along(stages)) {
    y <- top
    for (i in seq_len(nrow(counts))) {
      n <- counts[i, stages[[stage]]]
      nodes <- rbind(nodes, data.frame(stage, source = counts$source[[i]],
        x = x_stage[[stage]], ymin = y - n, ymax = y, n))
      y <- y - n
    }
  }
  links <- data.frame(id = character(), stage = integer(), source = character(), type = character(),
    n = numeric(), x0 = numeric(), x1 = numeric(), ymin0 = numeric(), ymax0 = numeric(), ymin1 = numeric(), ymax1 = numeric())
  drop_nodes <- data.frame(stage = integer(), source = character(), x = numeric(), ymin = numeric(), ymax = numeric(), n = numeric())
  for (stage in seq_len(length(stages) - 1L)) {
    previous <- nodes[nodes$stage == stage, ]
    next_nodes <- nodes[nodes$stage == stage + 1L, ]
    removed <- previous$n - next_nodes$n
    # Each removal endpoint is below the main stream and is a true terminal.
    drop_top <- -0.09 * top
    for (i in seq_len(nrow(counts))) {
      a <- previous[i, ]
      b <- next_nodes[i, ]
      loss <- removed[[i]]
      if (b$n > 0) links <- rbind(links, data.frame(
        id = paste(stage, a$source, "retained", sep = "_"),
        stage, source = a$source, type = "retained", n = b$n,
        x0 = a$x + width / 2, x1 = b$x - width / 2,
        ymin0 = a$ymax - b$n, ymax0 = a$ymax,
        ymin1 = b$ymin, ymax1 = b$ymax
      ))
      if (loss > 0) {
        links <- rbind(links, data.frame(
          id = paste(stage, a$source, "removed", sep = "_"),
          stage, source = a$source, type = "removed", n = loss,
          x0 = a$x + width / 2, x1 = b$x - width / 2,
          ymin0 = a$ymin, ymax0 = a$ymin + loss,
          ymin1 = drop_top - loss, ymax1 = drop_top
        ))
        drop_nodes <- rbind(drop_nodes, data.frame(stage = stage + 1L,
          source = a$source, x = b$x, ymin = drop_top - loss,
          ymax = drop_top, n = loss))
        drop_top <- drop_top - loss
      }
      stopifnot(b$n + loss == a$n)
    }
  }
  stopifnot(all(abs(links$ymax0 - links$ymin0 - links$n) < 1e-8),
            all(abs(links$ymax1 - links$ymin1 - links$n) < 1e-8))
  polygons <- if (!nrow(links)) data.frame(x = numeric(), y = numeric(), id = character(), source = character(), type = character()) else do.call(rbind, lapply(seq_len(nrow(links)), function(i) {
    a <- links[i, ]
    shape <- ggalluvial::positions_to_flow(
      a$x0, a$x1, a$ymin0, a$ymax0, a$ymin1, a$ymax1,
      kp0 = 0.25, kp1 = 0.25, knot.prop = TRUE,
      curve_type = "sine", curve_range = NULL, segments = 128
    )
    shape$id <- a$id
    shape$source <- a$source
    shape$type <- a$type
    shape
  }))
  main_bars <- data.frame(x = x_stage,
    ymin = vapply(stages, function(s) top - sum(counts[[s]]), numeric(1)), ymax = top)
  end_bars <- if (!nrow(drop_nodes)) data.frame(x = numeric(), ymin = numeric(), ymax = numeric()) else do.call(rbind, lapply(split(drop_nodes, drop_nodes$stage), function(a)
    data.frame(x = a$x[[1]], ymin = min(a$ymin), ymax = max(a$ymax))))
  bars <- rbind(main_bars, end_bars)
  bars <- bars[bars$ymax > bars$ymin, ]
  source_labels <- nodes[nodes$stage == 1L & nodes$n > 0, ]
  source_labels$label <- if (nrow(source_labels)) paste0(source_labels$source, "  ", source_labels$n) else character()
  final_labels <- nodes[nodes$stage == length(stages) & nodes$n > 0, ]
  final_labels$label <- if (nrow(final_labels)) paste0(final_labels$source, "  ", final_labels$n) else character()
  place_labels <- function(labels) {
    labels$anchor_y <- (labels$ymin + labels$ymax) / 2
    labels$label_y <- labels$anchor_y
    if (nrow(labels)) {
      labels$label_y[1] <- min(labels$label_y[1], top * 0.97)
      if (nrow(labels) > 1L) for (i in 2:nrow(labels)) {
        labels$label_y[i] <- min(labels$label_y[i], labels$label_y[i - 1L] - 0.08 * top)
      }
    }
    labels
  }
  source_labels <- place_labels(source_labels)
  final_labels <- place_labels(final_labels)
  stage_names <- c(collected = "Collected", unique = "Deduplicated",
                   input = "Input", evidence = "Source evidence", accepted = "Final dataset")
  drop_names <- c(unique = "Duplicates", evidence = "Not advanced", accepted = "Not accepted")
  losses <- vapply(seq_len(length(stages) - 1L), function(i)
    sum(counts[[stages[[i]]]]) - sum(counts[[stages[[i + 1L]]]]), numeric(1))
  headings <- data.frame(x = x_stage, y = top + 0.14 * top,
    label = unname(stage_names[stages]), n = colSums(counts[stages]))
  loss_labels <- data.frame(x = x_stage[-1],
    y = -0.09 * top - max(losses, 0) - 0.06 * top,
    label = unname(drop_names[stages[-1]]), n = losses)
  loss_labels <- loss_labels[loss_labels$n > 0, ]
  bottom <- -0.09 * top - max(losses, 0) - 0.19 * top
  
  p <- ggplot() +
    geom_polygon(data = polygons[polygons$type == "retained", ],
      aes(x, y, group = id, fill = source), colour = "white", linewidth = 0.22, alpha = 0.5) +
    geom_polygon(data = polygons[polygons$type == "removed", ],
      aes(x, y, group = id, fill = source), colour = "white", linewidth = 0.25, alpha = 0.5) +
    scale_fill_manual(values = colours, guide = "none") +
    geom_rect(data = bars, aes(xmin = x - width / 2, xmax = x + width / 2,
                             ymin = ymin, ymax = ymax), fill = "#2B2B2B", colour = NA) +
    geom_segment(data = source_labels[abs(source_labels$label_y - source_labels$anchor_y) > 1e-7, ],
      aes(x = x - 0.07, xend = x - width / 2, y = label_y, yend = anchor_y),
      colour = "#707070", linewidth = 0.15) +
    geom_segment(data = final_labels[abs(final_labels$label_y - final_labels$anchor_y) > 1e-7, ],
      aes(x = x + width / 2, xend = x + 0.07, y = anchor_y, yend = label_y),
      colour = "#707070", linewidth = 0.15) +
    geom_text(data = source_labels,
      aes(x = x - 0.08, y = label_y, label = label),
      hjust = 1, size = 2.8) +
    geom_text(data = final_labels,
      aes(x = x + 0.08, y = label_y, label = label),
      hjust = 0, size = 2.8) +
    geom_text(data = headings, aes(x, y, label = label), size = 3.0, fontface = "bold") +
    geom_text(data = headings, aes(x, y = y - label_gap, label = n), size = 3.0) +
    geom_text(data = loss_labels, aes(x, y, label = label), size = 2.8) +
    geom_text(data = loss_labels, aes(x, y = y - label_gap, label = n), size = 2.8) +
    scale_x_continuous(limits = c(-0.03, 7.35), expand = expansion(mult = 0)) +
    scale_y_continuous(limits = c(bottom, top + 0.20 * top), expand = expansion(mult = 0)) +
    coord_cartesian(clip = "off") +
    theme_void(base_family = "Helvetica", base_size = 9) +
    theme(plot.margin = margin(7, 7, 7, 7),
          plot.background = element_rect(fill = "white", colour = NA))
  
  quartz_pdf <- function(filename, width, height, ...) {
    grDevices::quartz(type = "pdf", file = filename, width = width, height = height, ...)
  }
  if (capabilities("aqua")) {
    ggsave(output, p, device = quartz_pdf, width = 7.2, height = 4.15, units = "in")
  } else if (capabilities("cairo")) {
    ggsave(output, p, device = grDevices::cairo_pdf, width = 7.2, height = 4.15, units = "in")
  } else {
    ggsave(output, p, device = grDevices::pdf, width = 7.2, height = 4.15,
           units = "in", useDingbats = FALSE)
  }
  stopifnot(file.exists(output))
  return(invisible(p))
  
}

if (sys.nframe() == 0L) {
  args <- commandArgs(trailingOnly = TRUE)
  if (length(args) != 2L) stop("Usage: Rscript plot_georeference_flow.R COUNTS.tsv OUTPUT.pdf")
  counts <- read.delim(args[[1]], check.names = FALSE, stringsAsFactors = FALSE)
  plot_georeference_flow(counts, args[[2]])
}
