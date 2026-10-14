import type { SiteRow } from "@/lib/types";

export type Geo = { type: string; coordinates: unknown } | null;
export type LeadGeometry = { title?: Geo; plot?: Geo; house?: Geo; access?: Geo; point?: [number, number] };
export type AcqConfidence = { score: number; label: string; cap_reason: string | null };
export type AcqComponent = { score: number; max: number; notes?: string[] };
export type Flag = { code: string; severity: string; title: string };

export type LeadSummary = {
  id: number;
  site_id: number;
  stage: string;
  type: string;
  type_label: string;
  ownership_status: string;
  score: number | null;
  components: Record<string, AcqComponent>;
  confidence: AcqConfidence | null;
  fatal: boolean;
  flags: Flag[];
  benefits: string[];
  contact_method: string | null;
  next_action: string | null;
  access: { type?: string; frontage_m?: number; road_name?: string | null; road_class?: string; corner?: boolean; position?: string; strip_length_m?: number } | null;
  metrics: Record<string, number | string | boolean | null> | null;
  constraints: { dataset: string; name: string | null }[];
  title_id: string | null;
  area: string | null;
  geometry: LeadGeometry;
  draft: { id: number; status: string; channel: string; kind: string } | null;
  reply_waiting: boolean;
  decline_reason: string | null;
  stage_changed_at: string | null;
  created_at: string;
  site: SiteRow | null;
};

export type ComplianceCheck = { code: string; label: string; status: "pass" | "fail" | "review" | "warn"; detail: string };
export type Outreach = {
  id: number;
  lead_id: number;
  contact_id: number | null;
  kind: string;
  channel: "post" | "email";
  status: string;
  recipient_type: string;
  recipient_name: string;
  recipient_address: string | null;
  recipient_email: string | null;
  recipient_hash: string;
  subject: string | null;
  body?: string;
  footer?: string | null;
  word_count: number | null;
  persona: string | null;
  generator: string | null;
  reference: string | null;
  created_at: string;
  approved_at: string | null;
  approved_by: string | null;
  sent_at: string | null;
  provider: string | null;
  provider_message_id: string | null;
  delivery_status: string | null;
  postage_estimate_gbp: number | null;
  review_reason: string | null;
  rejected_reason: string | null;
  compliance?: { verdict: string; checks: ComplianceCheck[]; issues: { code: string; severity: string; message: string }[]; checked_at: string; review_cleared?: { by: string; at: string; note: string } | null };
  generator_meta?: Record<string, unknown>;
  delivery_detail?: Record<string, unknown>;
  has_final_pdf?: boolean;
  lead_headline?: string;
};

export type Contact = { id: number; kind: string; name: string; salutation: string | null; address_lines: string[]; postcode: string | null; email: string | null; email_basis: string | null; source: string; source_ref: string | null; source_document_id: number | null; verified: boolean; verified_by: string | null; verified_at: string | null; retention_until: string | null; notes: string | null };

type Entry = { n: number; date: string | null; text: string };
export type TitleDoc = {
  id: number;
  filename: string;
  title_number: string | null;
  tenure: string | null;
  status: string;
  uploaded_at: string;
  uploaded_by: string | null;
  verified_at: string | null;
  verified_by: string | null;
  source_note: string | null;
  text_excerpt: string;
  extracted: {
    title_number?: string | null;
    edition_date?: string | null;
    official_copy_date?: string | null;
    tenure?: string | null;
    class_of_title?: string | null;
    property_description?: string | null;
    property_address?: string | null;
    proprietors?: { name: string; kind: string; company_number: string | null }[];
    proprietor_address?: string | null;
    proprietor_postcode?: string | null;
    proprietor_entry?: string | null;
    personal_representatives?: boolean;
    trustees?: boolean;
    price_paid?: { amount: number; date: string | null; kind: string; raw: string } | null;
    restrictions?: Entry[];
    charges?: (Entry & { lender: string | null })[];
    covenants?: Entry[];
    easements?: Entry[];
    notices?: Entry[];
    warnings?: string[];
  };
};

export type Inbound = { id: number; lead_id: number; outreach_id: number | null; channel: string; from_name: string | null; from_address: string | null; subject: string | null; body: string; received_at: string; category: string; category_source: string; category_confidence: number; evidence: string | null; decline_reason: string | null; handled: boolean; suggested_reply_id: number | null };

export type Brief = {
  available: boolean;
  note?: string;
  walk_away_gbp?: number;
  prudent_offer_gbp?: number;
  suggested_opening_range_gbp?: [number, number] | null;
  basis?: string;
  comparables?: { available: boolean; comparables_median?: number; p25?: number; p75?: number; n?: number; note?: string };
  biggest_risks?: { factor: string; reduces_value_by_gbp: number }[];
  unknowns_to_resolve?: string[];
  structures?: string[];
  questions_for_owner?: string[];
  rules?: string;
};

export type LeadDetail = LeadSummary & {
  analysis: Record<string, unknown> & { caveats?: string[]; sources?: Record<string, string>; inspire_ids?: string[] };
  acq_score: { components: Record<string, AcqComponent>; flags: Flag[]; confidence: AcqConfidence; total: number; type_label: string } | null;
  owner: Record<string, string> | null;
  notes: string | null;
  assessment: {
    planning: { classification: string; classification_label: string; summary: string; confidence: number } | null;
    residual: { residual_land_value: number; max_recommended_offer: number; prudent_offer: number; budget: number; market_value: { available: boolean; comparables_median?: number; note?: string } } | null;
    fit: { fits: boolean | null; site_area_m2?: number; method?: string } | null;
    fatal_flags: { code: string; title: string }[];
  };
  titles: TitleDoc[];
  contacts: Contact[];
  outreach: Outreach[];
  inbound: Inbound[];
  events: { at: string; from: string | null; to: string; actor: string; note: string | null }[];
  brief: Brief | null;
  postal: { provider: string; test_mode: boolean; estimate_gbp: number };
  mailbox: { provider: string; email: string } | null;
  buyer_confirmed: boolean;
};

export const STAGES = [
  "discovered",
  "feasibility_screened",
  "owner_verified",
  "draft_prepared",
  "awaiting_approval",
  "contacted",
  "awaiting_response",
  "interested",
  "feasibility_discussion",
  "negotiation",
  "under_offer",
  "completed",
  "closed",
];

export const STAGE_LABEL: Record<string, string> = {
  discovered: "Discovered",
  feasibility_screened: "Feasibility screened",
  owner_verified: "Owner verified",
  draft_prepared: "Draft prepared",
  awaiting_approval: "Awaiting my approval",
  contacted: "Contacted",
  awaiting_response: "Awaiting response",
  interested: "Interested",
  feasibility_discussion: "Feasibility discussion",
  negotiation: "Negotiation",
  under_offer: "Under offer",
  completed: "Completed",
  closed: "Closed",
};

export const OWNERSHIP_LABEL: Record<string, [string, string]> = {
  unknown: ["Owner unknown", "unknown"],
  inspire_matched: ["Title extent matched", "info"],
  title_purchase_needed: ["Official copy needed", "warn"],
  inferred_ccod: ["Company owner suggested (unconfirmed)", "warn"],
  verified: ["Owner verified (register)", "good"],
};

export const ACCESS_LABEL: Record<string, string> = {
  independent_frontage: "Own road frontage",
  classified_road_frontage: "Frontage to a classified road",
  private_lane: "Private lane",
  backland_strip: "Access strip (backland)",
  none_identified: "No access identified",
  unknown: "Access unknown",
};

export const CONTACT_METHOD_LABEL: Record<string, string> = {
  none_until_owner_verified: "None until the owner is verified",
  post: "Letter by post",
  email_or_post: "Email (permitted) or letter",
  manual_review: "Manual compliance review",
};

export const DRAFT_STATUS: Record<string, [string, string]> = {
  draft: ["Draft", "unknown"],
  needs_changes: ["Needs changes", "bad"],
  needs_compliance_review: ["Compliance review", "warn"],
  awaiting_approval: ["Awaiting your approval", "info"],
  approved: ["Approved — not yet sent", "good"],
  sending: ["Sending…", "info"],
  sent: ["Sent (accepted by provider)", "good"],
  posted: ["Posted", "good"],
  delivery_unknown: ["Delivery unconfirmed", "bad"],
  failed: ["Failed", "bad"],
  rejected: ["Rejected", "unknown"],
  withdrawn: ["Withdrawn", "unknown"],
  superseded: ["Superseded", "unknown"],
};

export const REPLY_CATEGORY: Record<string, [string, string]> = {
  interested: ["Interested", "good"],
  possibly_interested: ["Possibly interested", "good"],
  wants_information: ["Wants more information", "info"],
  requests_price: ["Requests a price", "warn"],
  not_interested: ["Not interested", "unknown"],
  requests_no_contact: ["Requests no further contact", "bad"],
  unknown: ["Unknown — needs review", "warn"],
};

export const DECLINE_LABEL: Record<string, string> = {
  not_selling: "Unwilling to sell",
  price_expectations: "Price expectations",
  privacy: "Loss of privacy / amenity",
  planning_concerns: "Planning concerns",
  timing: "Not the right time",
  family_reasons_unspecified: "Personal reasons (not recorded)",
  other: "Other",
};

export const COMP_LABEL: Record<string, string> = {
  planning: "Residential planning potential",
  physical: "Physical subdivision / redevelopment",
  economics: "Total development economics",
  ownership: "Ownership & acquisition practicality",
  home: "Suitability for your future home",
};
export const COMP_ORDER = ["planning", "physical", "economics", "ownership", "home"];
export const COMP_COLOR: Record<string, string> = { planning: "#2f5d50", physical: "#8a9a5b", economics: "#5d7f4a", ownership: "#3d5f8f", home: "#c9b27c" };
