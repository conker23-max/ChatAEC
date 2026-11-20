library(tidyverse)
library(stringr)

dossier_csv <- "."
fichier_sortie <- "amendements_agreges.csv"

# 1. Lister et trier les fichiers dans l'ordre numérique : libre_office.csv, (1), (2)...
fichiers <- list.files(
  path = dossier_csv,
  pattern = "^libre_office(\\(\\d+\\))?\\.csv$",
  full.names = TRUE
)

cle_tri <- function(chemin) {
  nom <- basename(chemin)
  m <- str_match(nom, "\\((\\d+)\\)")
  if (!is.na(m[1, 2])) {
    return(as.integer(m[1, 2]))
  }
  return(0L)
}

fichiers <- fichiers[order(map_int(fichiers, cle_tri))]

if (length(fichiers) == 0) {
  stop("Aucun fichier 'libre_office*.csv' trouvé dans le dossier.")
}

message("Fichiers détectés (", length(fichiers), ") :")
walk(fichiers, ~ message("  - ", basename(.x)))

# 2. Lecture robuste en forçant toutes les colonnes en character pour éviter les conflits de types
lire_csv_propre <- function(chemin) {
  read_csv(
    chemin,
    col_types = cols(.default = col_character()),
    show_col_types = FALSE
  ) %>%
    mutate(fichier_source = basename(chemin))
}

df_total <- map_dfr(fichiers, lire_csv_propre) %>%
  filter(!if_all(-fichier_source, ~ is.na(.x) | str_trim(.x) == ""))

avant_dedup <- nrow(df_total)

# 3. Déduplication sur l'URL XML si présente
if ("URL Amendement format XML" %in% colnames(df_total)) {
  df_total <- df_total %>%
    distinct(`URL Amendement format XML`, .keep_all = TRUE)
  message("Déduplication sur 'URL Amendement format XML' : ", avant_dedup, " -> ", nrow(df_total), " lignes.")
}

# 4. Export du CSV consolidé
write_csv(df_total, fichier_sortie, na = "")
message("Agrégation terminée : ", nrow(df_total), " lignes enregistrées dans '", fichier_sortie, "'.")