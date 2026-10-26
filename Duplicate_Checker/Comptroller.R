library(tidyverse)
library(janitor)
library(readxl)
library(stringr)
library(stringdist)
library(openxlsx)

Comptroller <- read_excel("C:/WORKING/git.non-hhs-projects/Practicum_2026.git/Duplicate_Checker/ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx",
                          guess_max = 100000) |>   #check all rows for column types, otherwise sparse text columns get read as logical and lose values
  clean_names()

#clean blank and NA values
Comptroller <- Comptroller |>
  mutate(across(where(is.character),
      ~ str_squish(.x))) |>
  mutate(across(where(is.character),
      ~ if_else(str_to_lower(.x) %in% c("", "n/a", "na"), NA_character_, .x)))

#normalize facility names
normalize_name <- function(x) {
  x |>
    str_to_lower() |>
    str_replace_all("(llc|pllc|plc|inc|clinic|center|healthcare|health|medical)", "") |>
    str_replace_all("[^a-z0-9 ]", "") |>
    str_squish()
}

#normalize addresses
normalize_addr <- function(x) {
  x |>
    str_to_lower() |>
    str_replace_all("street", "st") |>
    str_replace_all("road", "rd") |>
    str_replace_all("avenue", "ave") |>
    str_replace_all("boulevard", "blvd") |>
    str_replace_all("suite", "ste") |>
    str_replace_all("[^a-z0-9 ]", "") |>
    str_squish()
}

#create normalized matching fields
Comptroller <- Comptroller |>
  mutate(
    n_name    = normalize_name(organization_facility_name),
    n_street  = normalize_addr(organization_street_name),
    n_city    = str_to_lower(organization_city_name),
    n_state   = str_to_lower(organization_state),
    n_mstreet = normalize_addr(organization_mailing_address),
    n_mstate  = str_to_lower(organization_mailing_state))

Comptroller |>
  count(n_name, sort = TRUE) |>
  filter(n > 1) |>
  print(n = 50)

#identify checkbox columns
checkbox_cols <- names(Comptroller)[str_detect(names(Comptroller), "choice")]

length(checkbox_cols)

Comptroller |>
  select(all_of(checkbox_cols)) |>
  unlist() |>
  unique()

#completeness score
Comptroller <- Comptroller |>
  mutate(
    completeness_score =
      rowSums(!is.na(pick(
        organization_facility_name,
        organization_street_name,
        organization_city_name,
        organization_state,
        organization_zip_code))) +
      rowSums(!is.na(pick(
        organization_mailing_address,
        organization_mailing_state,
        organization_mailing_zip_code))) +
      rowSums(!is.na(pick(
        contains("phone"),
        contains("email"),
        contains("website"),
        contains("facebook"),
        contains("instagram"),
        contains("twitter"),
        contains("linkedin"),
        contains("youtube")))) * 0.5 +
      rowSums(pick(all_of(checkbox_cols)) == "Checked", na.rm = TRUE) * 0.05)

Comptroller |>
  select(record_id, organization_facility_name, completeness_score) |>
  arrange(desc(completeness_score)) |>
  print(n = 25)

#initialize duplicate grouping variables
Comptroller$duplicate_group_id <- NA_integer_
Comptroller$duplicate_reason   <- NA_character_

#work on plain vectors- indexing into a tibble inside a loop is slow
n          <- nrow(Comptroller)
group_id   <- rep(NA_integer_, n)
reason     <- rep(NA_character_, n)
n_name     <- Comptroller$n_name
n_street   <- Comptroller$n_street

#records can only match within the same city/state, so only compare within
#those blocks (records missing city or state never match anything)
city_key <- if_else(is.na(Comptroller$n_city) | is.na(Comptroller$n_state),
  NA_character_, paste(Comptroller$n_city, Comptroller$n_state, sep = "|"))
city_blocks <- split(seq_len(n), city_key)

gid <- 1L

for (i in seq_len(n)) {
  if (!is.na(group_id[i])) next
  group_id[i] <- gid

  if (!is.na(city_key[i])) {
    #unassigned records in the same city/state
    cand <- city_blocks[[city_key[i]]]
    cand <- cand[cand != i & is.na(group_id[cand])]

    if (length(cand) > 0) {
      #similarity scores against all candidates at once- 0 when missing
      name_sim <- stringsim(n_name[i], n_name[cand], method = "jw")
      phys_sim <- stringsim(n_street[i], n_street[cand], method = "jw")
      name_sim[is.na(name_sim)] <- 0
      phys_sim[is.na(phys_sim)] <- 0

      #assign duplicate group if name and/or physical address are similar
      hit <- name_sim >= 0.90 | phys_sim >= 0.90
      group_id[cand[hit]] <- gid
      reason[cand[hit]] <- case_when(
        name_sim[hit] >= 0.90 & phys_sim[hit] >= 0.90 ~ "Name + Physical Address",
        name_sim[hit] >= 0.90 ~ "Name",
        phys_sim[hit] >= 0.90 ~ "Physical Address",
        TRUE ~ NA_character_)
    }
  }
  gid <- gid + 1L
}

Comptroller$duplicate_group_id <- group_id
Comptroller$duplicate_reason   <- reason

Comptroller <- Comptroller |>
  group_by(duplicate_group_id) |>
  mutate(
    duplicate_group_size = n(),
    recommended_primary_record = if_else(
      completeness_score == max(completeness_score, na.rm = TRUE),
      "YES", "NO"),
    review_needed = if_else(duplicate_group_size > 1, "YES", "NO")) |>
  ungroup()

Comptroller <- Comptroller |>
  group_by(duplicate_group_id) |>
  mutate(
    duplicate_reason = if_else(
      review_needed == "YES",
      paste(unique(na.omit(duplicate_reason)), collapse = "; "),
      NA_character_)) |>
  ungroup()

possible_duplicates <- Comptroller |>
  filter(review_needed == "YES") |>
  arrange(duplicate_group_id, desc(completeness_score))

possible_duplicates_review <- possible_duplicates |>
  select(
    duplicate_group_id,
    duplicate_group_size,
    record_id,
    organization_facility_name,
    organization_street_name,
    organization_city_name,
    organization_state,
    organization_zip_code,
    organization_mailing_address,
    organization_mailing_state,
    organization_mailing_zip_code,
    duplicate_reason,
    completeness_score,
    recommended_primary_record,
    review_needed) |>
  arrange(duplicate_group_id, desc(completeness_score))

unique_records <- Comptroller |>
  filter(review_needed == "NO")

Comptroller |>
  count(review_needed)

Comptroller |>
  filter(review_needed == "YES") |>
  distinct(duplicate_group_id) |>
  nrow()

Comptroller |>
  filter(review_needed == "YES") |>
  count(duplicate_group_size, sort = TRUE)

#export review workbook- duplicate groups plus an empty tracking tab to fill in
tracking_template <- tibble(
  `Redcap ID` = character(),
  `potential duplicate with? \n(tip: search for potential duplicates by name and address)` = character(),
  `which is true duplicate?` = character(),
  `which record is most complete record?\n (mark as not duplicate, verify info, ud` = character(),
  `records that are not actual duplicates` = character(),
  `Old name (previously in redcap)` = character(),
  `New name` = character(),
  `Old address (previously in redcap)` = character(),
  `New address` = character(),
  `Mailing Address` = character(),
  `other changes` = character(),
  `validated date?` = character(),
  `notes (add to redcap)` = character())

output_file <- paste0("C:/WORKING/git.non-hhs-projects/Practicum_2026.git/Duplicate_Checker/Comptroller_Duplicate_Groups_",
                      format(Sys.Date(), "%m-%d-%Y"), ".xlsx")

wb <- createWorkbook()
header_style <- createStyle(textDecoration = "bold", fgFill = "#D9E1F2", wrapText = TRUE)

addWorksheet(wb, "Assigned Duplicate Groups")
writeData(wb, "Assigned Duplicate Groups", possible_duplicates_review,
          headerStyle = header_style, withFilter = TRUE)
freezePane(wb, "Assigned Duplicate Groups", firstRow = TRUE)
setColWidths(wb, "Assigned Duplicate Groups",
             cols = seq_along(possible_duplicates_review), widths = "auto")

addWorksheet(wb, "Tracking Duplicates")
writeData(wb, "Tracking Duplicates", tracking_template, headerStyle = header_style)
freezePane(wb, "Tracking Duplicates", firstRow = TRUE)
setColWidths(wb, "Tracking Duplicates", cols = seq_along(tracking_template), widths = 25)

saveWorkbook(wb, output_file, overwrite = TRUE)
cat("Saved:", output_file, "\n")
