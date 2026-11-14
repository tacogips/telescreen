import type { Member, Team } from "../../types";

export const memberFixtures: Member[] = [
  { id: "mem-01", handle: "alex", display_name: "Alex Morgan", email: ["j***", "@example.com"].join(""), slack_user_id: "U-01", active: true },
  { id: "mem-02", handle: "sam", display_name: "Sam Taylor", email: ["s***", "@example.org"].join(""), slack_user_id: null, active: true },
];
export const teamFixtures: Team[] = [
  { id: "team-01", slug: "engineering", name: "Engineering", email: ["e***", "@example.com"].join(""), slack_channel: "#engineering" },
  { id: "team-02", slug: "security", name: "Security", email: ["s***", "@example.org"].join(""), slack_channel: "#security" },
];
