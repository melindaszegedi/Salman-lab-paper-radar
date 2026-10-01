// Settings for accounts. Fill these in after creating your Supabase project (see README, step "Accounts").
// Both values are meant to be public: the publishable/anon key only allows what the database
// rules in supabase/schema.sql allow (each person can read and edit only their own preferences).
// Never put the secret / service_role key here.
window.RADAR_CONFIG = {
  supabaseUrl: "",   // e.g. "https://abcdefghijkl.supabase.co"
  supabaseKey: ""    // the "publishable" key (sb_publishable_...) or the legacy "anon" key
};
