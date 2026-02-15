"""
RAG documentation sections for BillQode architecture.

Each section has:
- id: unique identifier
- department: which team owns this doc (e.g. "backend", "frontend", "mobile", "devops")
- keywords: trigger words for keyword-based retrieval
- content: detailed documentation text

Only sections matching the Jira ticket are included in the LLM prompt,
reducing token usage while keeping context precise.

HOW TO ADD A NEW DEPARTMENT:
1. Add sections to the appropriate list below (e.g. FRONTEND_SECTIONS).
2. Each section needs: id, department, keywords, content.
3. The section is auto-included in SECTIONS and will be matched by keywords.
"""

from __future__ import annotations

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BACKEND — Laravel 11, Clean Architecture + DDD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACKEND_SECTIONS: list[dict[str, object]] = [
    {
        "id": "architecture_overview",
        "department": "backend",
        "keywords": [
            "architecture", "layer", "structure", "namespace", "module",
            "application", "domain", "infrastructure", "clean architecture",
            "ddd", "design", "organize", "folder", "directory",
        ],
        "content": """\
ARCHITECTURE OVERVIEW:
Laravel 11 (PHP 8.2+) — Clean Architecture + Domain-Driven Design.

src/
├── Application/      # Controllers, Requests, Resources, Jobs, Commands
├── Domain/          # Actions, DTOs, Enums, Models, Business Logic
└── Infrastructure/  # Services, Repositories, External Integrations

Namespaces:
- Application\\ — Entry points and presentation logic
- Domain\\ — Business logic and domain entities
- Infrastructure\\ — Technical implementations and external dependencies

Rules:
- No business logic in Application layer (controllers are thin).
- No database access outside Infrastructure/Repositories.
- Domain layer contains only business logic.
- Strict layer separation must be maintained at all times.""",
    },
    {
        "id": "action_pattern",
        "department": "backend",
        "keywords": [
            "action", "business logic", "execute", "readonly",
            "use case", "handler", "operation", "crud", "create",
            "update", "delete", "store", "process",
        ],
        "content": """\
ACTION PATTERN (src/Domain/{Module}/Actions/):
All business logic is in dedicated Action classes.

```php
readonly class {Action}Action
{
    public function __construct(
        private {Repository}RepositoryInterface $repository,
    ) {}

    public function execute({Parameters}): {ReturnType}
    {
        // Single, focused responsibility
    }
}
```

Rules:
- Actions MUST be readonly classes.
- Actions MUST have a single execute() method.
- Dependencies injected via constructor.
- Use Repository pattern for data access (never query DB directly).""",
    },
    {
        "id": "dto_pattern",
        "department": "backend",
        "keywords": [
            "dto", "data transfer", "toarray", "camelcase",
            "snake_case", "mapping", "transform", "payload",
            "create", "store", "update", "crud",
        ],
        "content": """\
DTO PATTERN (src/Domain/{Module}/Dto/):
All data transfer between layers uses DTOs.

```php
class {Module}Dto extends Domain\\Common\\Dto\\Dto
{
    public function __construct(
        public ?int $id = null,
        public ?string $name = null,
    ) {}

    public function toArray(): array
    {
        return array_filter([
            'id' => $this->id,
            'name' => $this->name,
        ], fn($value) => !is_null($value));
    }
}
```

Rules:
- All DTOs MUST extend Domain\\Common\\Dto\\Dto.
- Properties MUST be public and typed with defaults (usually null).
- MUST implement toArray() mapping camelCase properties to snake_case keys.
- Located in src/Domain/{Module}/Dto/.""",
    },
    {
        "id": "repository_pattern",
        "department": "backend",
        "keywords": [
            "repository", "database", "query", "eloquent", "db",
            "persist", "fetch", "find", "interface", "data access",
            "collection", "pagination", "crud", "create", "update",
            "delete", "store",
        ],
        "content": """\
REPOSITORY PATTERN (src/Infrastructure/Repositories/{Module}/):
All database access goes through repository interfaces.

```php
// Interface first
interface {Module}RepositoryInterface
{
    public function create(array $data): {Model};
    public function update(int $id, array $data): bool;
    public function findById(int $id): ?{Model};
    public function list(array $filters): Collection;
}

// Then implementation
class {Module}Repository implements {Module}RepositoryInterface
{
    public function create(array $data): {Model}
    {
        return {Model}::create($data);
    }
}
```

Rules:
- Always create the interface first.
- Bind interface to implementation in a service provider.
- All DB operations MUST go through repositories (never in Actions/Controllers directly).""",
    },
    {
        "id": "controller_pattern",
        "department": "backend",
        "keywords": [
            "controller", "endpoint", "route", "api", "http",
            "request", "response", "rest", "restful", "get", "post",
            "put", "patch", "crud",
        ],
        "content": """\
CONTROLLER PATTERN (src/Application/Http/Clients/Controllers/{Module}/):
Controllers are thin — delegate to Actions.

```php
class {Module}Controller extends Controller
{
    public function store(
        {Action}Request $request,
        {Action}Action $action
    ): JsonResponse {
        $data = $request->validatedInCamelCase();
        $result = $action->execute(new {Module}Dto(...$data));
        return api_response(
            new {Resource}($result),
            message: trans('general.successAction')
        );
    }
}
```

Rules:
- Controllers MUST be thin (no business logic).
- Use Form Requests for validation.
- Use Resources for response formatting.
- Use api_response() helper for consistent JSON responses.
- Inject Action and Request via method parameters.""",
    },
    {
        "id": "request_pattern",
        "department": "backend",
        "keywords": [
            "request", "validation", "form request", "rules",
            "validate", "input", "sanitize", "authorize",
        ],
        "content": """\
REQUEST VALIDATION PATTERN (src/Application/Http/Clients/Requests/{Module}/):

```php
class {Action}Request extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'email' => ['required', 'email', 'unique:users,email'],
        ];
    }

    public function validatedInCamelCase(): array
    {
        return array_map_keys_camelCase($this->validated());
    }
}
```

Rules:
- MUST extend FormRequest.
- Validation logic only (no business logic).
- Use validatedInCamelCase() for DTO compatibility.""",
    },
    {
        "id": "resource_pattern",
        "department": "backend",
        "keywords": [
            "resource", "json resource", "response format", "output",
            "transform", "serialize", "api response",
        ],
        "content": """\
RESOURCE PATTERN (src/Application/Http/Clients/Resources/{Module}/):

```php
class {Module}Resource extends JsonResource
{
    public function toArray($request): array
    {
        return [
            'id' => $this->id,
            'name' => $this->name,
            'createdAt' => $this->created_at?->toIso8601String(),
        ];
    }
}
```

Rules:
- Output MUST be camelCase.
- Dates MUST be ISO8601 format.
- Located in src/Application/Http/Clients/Resources/{Module}/.""",
    },
    {
        "id": "enum_pattern",
        "department": "backend",
        "keywords": [
            "enum", "constant", "status", "type", "state",
            "backed enum", "cases",
        ],
        "content": """\
ENUM PATTERN (src/Domain/{Module}/Enums/):

```php
enum {Name}Enum: string
{
    case ACTIVE = 'active';
    case INACTIVE = 'inactive';

    public static function values(): array
    {
        return array_column(self::cases(), 'value');
    }
}
```

Rules:
- Use PHP 8.2 backed enums (string or int).
- Provide helper methods (values(), labels()) as needed.
- Located in src/Domain/{Module}/Enums/.""",
    },
    {
        "id": "service_pattern",
        "department": "backend",
        "keywords": [
            "service", "integration", "external", "third-party",
            "api call", "complex operation", "webhook", "sdk",
        ],
        "content": """\
SERVICE PATTERN (src/Infrastructure/Services/{Module}/):
For complex operations and external integrations.

```php
class {Module}Service
{
    public function __construct(
        private DependencyInterface $dependency,
    ) {}

    public function performComplexOperation(): mixed
    {
        // Complex business logic
        // External API calls
        // Multi-step operations
    }
}
```

Rules:
- Used for operations not suitable for a single Action.
- Used for third-party integrations (APIs, SDKs).
- Located in src/Infrastructure/Services/{Module}/.""",
    },
    {
        "id": "model_pattern",
        "department": "backend",
        "keywords": [
            "model", "eloquent", "fillable", "casts", "relationship",
            "belongs to", "has many", "soft delete", "migration",
            "table", "column", "schema", "database", "create",
            "new feature", "entity",
        ],
        "content": """\
MODEL PATTERN (src/Domain/{Module}/Models/):

```php
class {Model} extends Model
{
    use SoftDeletes;

    protected $fillable = ['name', 'description'];

    protected $casts = [
        'is_active' => 'boolean',
        'meta' => 'array',
    ];

    public function client(): BelongsTo
    {
        return $this->belongsTo(Client::class);
    }
}
```

Rules:
- Located in src/Domain/{Module}/Models/.
- Use SoftDeletes when appropriate.
- Define $fillable, $casts, relationships.
- Database columns use snake_case.""",
    },
    {
        "id": "implementation_order",
        "department": "backend",
        "keywords": [
            "implementation", "order", "step", "workflow", "plan",
            "phase", "sequence", "new feature", "build", "scaffold",
            "create", "crud", "implement", "add", "feature",
        ],
        "content": """\
IMPLEMENTATION ORDER (MANDATORY — follow this exact sequence):

Phase 1 — Domain Layer:
1. Create/Update Enums (if needed)
2. Create/Update Models (define DB structure, relationships, casts)
3. Create Migration (Laravel naming conventions, add indexes)
4. Create DTOs (data contracts, toArray() method)
5. Create Repository Interface (define data access methods)
6. Create Repository Implementation (implement interface)
7. Create Actions (business logic, readonly, inject dependencies)

Phase 2 — Application Layer:
8. Create Form Requests (validation rules)
9. Create Resources (API response formatting)
10. Create Controller (thin, delegate to Actions)
11. Define Routes (RESTful, proper middleware)

Phase 3 — Infrastructure (if needed):
12. Create Services (complex ops, external integrations)
13. Create Jobs (async operations, ShouldQueue)
14. Create Commands (CLI operations)

Phase 4 — Testing:
15. Write unit tests for Actions
16. Write feature tests for API endpoints
17. Target >80% code coverage""",
    },
    {
        "id": "testing_strategy",
        "department": "backend",
        "keywords": [
            "test", "testing", "unit test", "feature test", "phpunit",
            "coverage", "assert", "mock", "factory", "tdd",
        ],
        "content": """\
TESTING STRATEGY:

Unit Tests (tests/Unit/Domain/{Module}/Actions/):
```php
class {Action}ActionTest extends TestCase
{
    public function test_it_executes_successfully(): void
    {
        $dto = new {Module}Dto(name: 'Test');
        $action = app({Action}Action::class);
        $result = $action->execute($dto);
        $this->assertNotNull($result);
    }
}
```

Feature Tests (tests/Feature/{Module}/):
```php
class {Module}ApiTest extends TestCase
{
    public function test_authenticated_user_can_create_{module}(): void
    {
        $user = ClientUser::factory()->create();
        $response = $this->actingAs($user)
            ->postJson('/api/clients/{module}', ['name' => 'Test']);
        $response->assertStatus(200);
        $this->assertDatabaseHas('{modules}', ['name' => 'Test']);
    }
}
```

Requirements:
- Unit tests for all Actions.
- Feature tests for all API endpoints.
- Test validation rules and authorization.
- Target >80% code coverage.""",
    },
    {
        "id": "error_handling",
        "department": "backend",
        "keywords": [
            "error", "exception", "try", "catch", "throw", "log",
            "logging", "bug", "fix", "debug", "handle",
        ],
        "content": """\
ERROR HANDLING:

Exception classes (Infrastructure/Exceptions/Custom/):
```php
class {Module}Exception extends \\Exception
{
    public static function {specificError}(string $detail): self
    {
        return new self("Error message: {$detail}");
    }
}
```

Usage in Actions:
```php
try {
    // Operation
} catch (\\Exception $e) {
    Log::error('Operation failed', [
        'error' => $e->getMessage(),
        'trace' => $e->getTraceAsString(),
    ]);
    throw {Module}Exception::{specificError}($e->getMessage());
}
```

Rules:
- Use specific exception classes per module.
- Always log error details with context.
- Never swallow exceptions silently.""",
    },
    {
        "id": "pos_integration",
        "department": "backend",
        "keywords": [
            "pos", "foodics", "square", "point of sale",
            "pos integration", "factory pattern",
        ],
        "content": """\
POS INTEGRATION (Foodics, Square):
- Use Factory pattern (PosIntegrationManager).
- Implement provider-specific services.
- Store OAuth tokens securely.
- Handle webhooks appropriately.
- Log all external API calls.""",
    },
    {
        "id": "payment_integration",
        "department": "backend",
        "keywords": [
            "payment", "gateway", "stripe", "transaction",
            "checkout", "billing", "invoice", "refund",
        ],
        "content": """\
PAYMENT GATEWAY INTEGRATION:
- Store transaction logs.
- Handle callbacks securely.
- Implement idempotency for payment operations.
- Handle failures gracefully with retries.
- Support multiple payment providers.""",
    },
    {
        "id": "notification_integration",
        "department": "backend",
        "keywords": [
            "notification", "whatsapp", "sms", "email", "message",
            "ultramsg", "push notification", "alert",
        ],
        "content": """\
WHATSAPP / NOTIFICATION INTEGRATION:
- Queue messages for async sending (Jobs + ShouldQueue).
- Support multiple providers (UltraMsg, Legacy).
- Log all notification attempts.
- Handle failures with retries.
- Use Laravel notification channels where possible.""",
    },
    {
        "id": "code_style",
        "department": "backend",
        "keywords": [
            "style", "psr-12", "naming", "convention", "format",
            "lint", "pint", "indentation", "import",
        ],
        "content": """\
CODE STYLE (PSR-12):
- 4 spaces indentation (no tabs).
- Unix line endings (LF).
- Files end with single blank line.
- Max 120 chars per line (soft limit).

Naming:
- Classes: PascalCase
- Methods/Variables: camelCase
- Constants: UPPER_SNAKE_CASE
- Database columns: snake_case
- API response keys: camelCase
- Routes: kebab-case

Type declarations required:
```php
public function method(string $param, ?int $optional = null): array
```

Imports: alphabetically sorted, grouped by vendor.""",
    },
    {
        "id": "api_helpers",
        "department": "backend",
        "keywords": [
            "api_response", "helper", "translation", "auth",
            "sanctum", "middleware", "rate limit",
        ],
        "content": """\
COMMON HELPERS:

API Response:
```php
api_response(data: $data, status: 200, message: 'Success', meta: [])
```

Translation:
```php
trans('general.successAction')
trans('validation.required')
```

Authentication (Sanctum):
```php
auth()->user()           // Current user
auth()->user()->client_id // Current client
auth()->check()          // Is authenticated?
```

Logging:
```php
Log::info('Message', ['context' => $data]);
Log::error('Error', ['error' => $e->getMessage()]);
```""",
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  FRONTEND — (Future: add Next.js / React / Vue rules here)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRONTEND_SECTIONS: list[dict[str, object]] = [
    # Example — uncomment and fill when ready:
    # {
    #     "id": "frontend_component_pattern",
    #     "department": "frontend",
    #     "keywords": ["component", "react", "vue", "tsx", "jsx", "ui"],
    #     "content": "...",
    # },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MOBILE — (Future: add Flutter / React Native / Swift rules here)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MOBILE_SECTIONS: list[dict[str, object]] = [
    # Example — uncomment and fill when ready:
    # {
    #     "id": "mobile_screen_pattern",
    #     "department": "mobile",
    #     "keywords": ["screen", "flutter", "widget", "navigation", "mobile"],
    #     "content": "...",
    # },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DEVOPS — (Future: add CI/CD, Docker, AWS, Terraform rules here)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEVOPS_SECTIONS: list[dict[str, object]] = [
    # Example — uncomment and fill when ready:
    # {
    #     "id": "devops_pipeline_pattern",
    #     "department": "devops",
    #     "keywords": ["pipeline", "ci", "cd", "deploy", "docker", "terraform"],
    #     "content": "...",
    # },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  COMBINED — all departments merged into one list for RAG retrieval
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECTIONS: list[dict[str, object]] = (
    BACKEND_SECTIONS
    + FRONTEND_SECTIONS
    + MOBILE_SECTIONS
    + DEVOPS_SECTIONS
)
