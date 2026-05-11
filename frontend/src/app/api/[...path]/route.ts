import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, await params);
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return handleProxy(request, await params);
}

async function handleProxy(request: NextRequest, params: { path: string[] }) {
  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const apiToken = process.env.ADMIN_API_TOKEN || "";

  // 1. URLを再構築 (例: /api/v1/portfolio/summary)
  const pathString = params.path.join("/");
  const searchParams = request.nextUrl.searchParams.toString();
  const targetUrl = `${backendUrl}/api/${pathString}${searchParams ? `?${searchParams}` : ""}`;

  // 2. ヘッダーの構築
  const headers = new Headers(request.headers);
  headers.set("Authorization", `Bearer ${apiToken}`);
  
  // 不要なヘッダー（ホストなど）を削除
  headers.delete("host");

  try {
    const fetchOptions: RequestInit = {
      method: request.method,
      headers: headers,
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      fetchOptions.body = await request.blob();
    }

    const response = await fetch(targetUrl, fetchOptions);

    // 3. バックエンドのレスポンスをそのまま返す
    const data = await response.blob();
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Access-Control-Allow-Origin", "*");

    return new NextResponse(data, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("[API Proxy Error]", error);
    return NextResponse.json(
      { error: "Failed to connect to backend service" },
      { status: 502 }
    );
  }
}
