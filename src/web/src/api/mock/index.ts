import { routeMockRequest } from "./router";

export const MOCK_FIXTURE_MARKER = "telescreen-mock-fixtures-v1";

export async function mockFetch(path: string, init: RequestInit): Promise<Response> {
  const response = await routeMockRequest(path, init);
  const headers = new Headers(response.headers);
  headers.set("X-Telescreen-Mock-Fixtures", MOCK_FIXTURE_MARKER);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}
