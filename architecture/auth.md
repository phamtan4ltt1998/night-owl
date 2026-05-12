# Auth API Flow Diagrams

## POST /auth/google

Upsert user từ Google OAuth userinfo, trả JWT + profile.

```mermaid
flowchart TD
    Client[Client] -->|POST /auth/google\nbody: email, name?, picture?, sub?| Handler

    Handler --> Upsert["get_or_create_user(email, name, picture)\n→ MySQL INSERT or SELECT"]
    Upsert --> UserRow[(users table)]
    UserRow --> CreateToken["_create_token(email, user_id)\nHS256 JWT, exp=30d"]
    CreateToken --> Response["200 OK\n{ access_token, token_type, user }"]

    Upsert -- error --> 400[400 Bad Request]
```

**JWT payload:** `{ sub: email, uid: user_id, exp: now+30d }`

---

## POST /auth/facebook

Upsert user từ Facebook userinfo. Email synthetic nếu không có real email.

```mermaid
flowchart TD
    Client[Client] -->|POST /auth/facebook\nbody: email?, name?, username?, picture?, facebook_id?| Handler

    Handler --> ResolveEmail{Has email?}
    ResolveEmail -- yes --> UseEmail[use email directly]
    ResolveEmail -- no --> HasUsername{Has username?}
    HasUsername -- yes --> SyntheticUser["email = fb_{username}@nightowl.local"]
    HasUsername -- no --> HasFbId{Has facebook_id?}
    HasFbId -- yes --> SyntheticFbId["email = fb_{facebook_id}@nightowl.local"]
    HasFbId -- no --> 400[400 Bad Request\nEmail/username/facebook_id required]

    UseEmail --> Upsert
    SyntheticUser --> Upsert
    SyntheticFbId --> Upsert

    Upsert["get_or_create_user(email, name, picture)\n→ MySQL INSERT or SELECT"] --> UserRow[(users table)]
    UserRow --> CreateToken["_create_token(email, user_id)\nHS256 JWT, exp=30d"]
    CreateToken --> Response["200 OK\n{ access_token, token_type, user }"]
```

---

## JWT Validation (dependency: `get_current_user`)

Used by all protected endpoints via `Depends(get_current_user)`.

```mermaid
flowchart TD
    Req[Request] --> HasCreds{Bearer token present?}
    HasCreds -- no --> 401A[401 Not authenticated]
    HasCreds -- yes --> Decode["jwt.decode(token, JWT_SECRET, HS256)"]
    Decode -- JWTError --> 401B[401 Token expired or invalid]
    Decode -- ok --> ExtractEmail{email in payload?}
    ExtractEmail -- no --> 401C[401 Invalid token]
    ExtractEmail -- yes --> FetchUser["get_or_create_user(email)\n→ MySQL"]
    FetchUser --> ReturnUser[Return user dict to handler]
```
