library(tidyverse)
library(janitor)
library(readxl)
library(stringr)
library(stringdist)
library(openxlsx)

Comptroller <- read_excel("C:/Users/cstratemann/Downloads/ComptrollerProject20_DATA_LABELS_2026-04-16_1051.xlsx") |>
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

gid <- 1

for (i in seq_len(nrow(Comptroller))) {
  if (!is.na(Comptroller$duplicate_group_id[i])) next
  Comptroller$duplicate_group_id[i] <- gid
  for (j in seq_len(nrow(Comptroller))) {
    if (i == j || !is.na(Comptroller$duplicate_group_id[j])) next
    
    #similarity scores- set to 0 when one/both values are missing
    name_sim <- stringsim(Comptroller$n_name[i], Comptroller$n_name[j], method = "jw")
    phys_sim <- stringsim(Comptroller$n_street[i], Comptroller$n_street[j], method = "jw")
    name_sim <- ifelse(is.na(name_sim), 0, name_sim)
    phys_sim <- ifelse(is.na(phys_sim), 0, phys_sim)
    
    #city/state match- set to FALSE when missing
    same_city <- Comptroller$n_city[i] == Comptroller$n_city[j] &
      Comptroller$n_state[i] == Comptroller$n_state[j]
    same_city <- ifelse(is.na(same_city), FALSE, same_city)
    
    #assign duplicate group if name and/or physical address are similar
    if ((name_sim >= 0.90 & same_city) |
        (phys_sim >= 0.90 & same_city)) {
      
      Comptroller$duplicate_group_id[j] <- gid
      
      Comptroller$duplicate_reason[j] <- case_when(
        name_sim >= 0.90 & phys_sim >= 0.90 ~ "Name + Physical Address",
        name_sim >= 0.90 ~ "Name",
        phys_sim >= 0.90 ~ "Physical Address",
        TRUE ~ NA_character_)
      }
  }
  gid <- gid + 1
}

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
