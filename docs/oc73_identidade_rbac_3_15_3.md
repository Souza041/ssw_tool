# OC73 — Identidade corporativa e RBAC (Sprint 3.15.3)

Data: 2026-10-16. Revisão exclusivamente local/offline. Nenhum IdP, SSW ou banco
foi acessado e nenhum arquivo de runtime foi alterado.

## 1. Diagnóstico da autenticação atual

| Propriedade | Classificação | Evidência/conclusão |
|---|---|---|
| Login no SSW | **COMPROVADO** | `web/routes.py` recebe domínio, CPF, usuário, senha e unidade; cria `SSWClient` e chama `login()`/`open_menu()`. |
| Sessão web assinada | **COMPROVADO** | `web/main.py` instala `SessionMiddleware` com segredo validado por `web/session_config.py`. |
| Sessão SSW server-side | **COMPROVADO** | `web/session_store.py` guarda o cliente em dicionário de memória por UUID e expira após oito horas. |
| Invalidação no logout | **COMPROVADO** | remove cliente/timestamp e limpa `request.session`. |
| Renovação após login | **COMPROVADO** | a sessão anterior é removida antes de criar novo UUID, reduzindo fixação. |
| Allowlist OC73 | **COMPROVADO** | usuário SSW é comparado de forma exata/normalizada; allowlist vazia nega acesso; só protege observabilidade. |
| RBAC central | **PENDENTE** | não existe modelo de papéis corporativos no repositório. |
| OIDC/OAuth2/SAML | **PENDENTE** | não há dependência, issuer, discovery, client ID, redirect URI, validação de token ou contrato local. |
| Integração de identidade Nexus | **PENDENTE** | “Nexus” aparece em UI/comentários, mas não há contrato verificável de identidade. Os comentários encontrados reutilizam login SSW. |
| Identidade imutável | **PENDENTE** | `client.usuario` é nome textual; não equivale a `(issuer, subject)`. |
| Vínculo conta-pessoa | **PENDENTE** | não existe identificador corporativo canônico de pessoa. E-mail/username não são suficientes. |
| MFA/reautenticação reforçada | **PENDENTE** | nenhuma evidência local comprova MFA ou instante de autenticação forte. |
| Auditoria de dois aceites | **PENDENTE** | não há trilha transacional de aprovações. |

O cookie assinado contém apenas o identificador local da sessão SSW. A senha não
é colocada diretamente no cookie, mas o objeto `SSWClient`, que mantém o contexto
autenticado, reside na memória de um único processo. Reinício ou outro worker não
compartilha esse estado.

## 2. Auditoria web

| Item | Resultado | Risco / patch futuro proposto |
|---|---|---|
| `Secure` | **NÃO CONFORME PARA APROVAÇÃO** | `https_only` não é informado; Starlette usa `False`. Em sprint separada: `https_only=True` após comprovar HTTPS fim a fim/proxy. |
| `HttpOnly` | **COMPROVADO** | Starlette inclui `httponly` por padrão. |
| `SameSite` | **INFERIDO/COMPROVADO NA VERSÃO INSTALADA** | padrão efetivo `lax`. Para aprovação sensível, propor `strict` após testes de login/redirect. |
| Erro de autenticação | **NÃO CONFORME** | `str(exc)` é enviado ao template. Propor mensagem fixa e log saneado apenas com tipo/correlation ID. |
| Fixação de sessão | **MITIGADA NO LOGIN** | sessão anterior é removida antes da nova. Ainda é necessário testar rotação com IdP futuro. |
| Expiração | **COMPROVADO LOCALMENTE** | oito horas por relógio monotônico; insuficiente para aceite crítico. Proposta: step-up/reautenticação de no máximo 15 minutos. |
| Logout | **COMPROVADO LOCALMENTE** | invalida armazenamento local; a rota é GET e pode sofrer logout CSRF, embora não crie aprovação. |
| CSRF | **PENDENTE/NÃO EXISTE PARA FUTURAS AÇÕES** | aprovar/revogar/executar devem ser POST, token CSRF vinculado à sessão e validação de `Origin`/`Host`; `SameSite` não substitui token. |
| Rate limiting | **PENDENTE** | não há controle visível no login. Definir limite por conta/origem no proxy e aplicação, sem revelar existência de usuário. |
| Autorização server-side | **COMPROVADA SÓ NA OBSERVABILIDADE** | o painel revalida allowlist por requisição; futuras operações devem revalidar claims/RBAC no consumo. |

O protótipo em `prototypes/oc73_identidade_rbac.py` define apenas o comportamento
seguro esperado. Ele não corrige nem é importado pelo login atual.

## 3. Alternativas de identidade

| Alternativa | Identidade/pessoa | Papéis/MFA/auditoria | Operação e disponibilidade | Parecer |
|---|---|---|---|---|
| A. Reutilizar login SSW local | username textual; sem `person_id` comprovado | sem RBAC/MFA verificável; sessão em memória | simples, mas acoplada a um processo e ao SSW | **INADEQUADA** para aprovações |
| B. IdP corporativo OIDC/OAuth2 | `(iss, sub)` estável; identificador canônico de pessoa depende de contrato corporativo adicional | claims/grupos, MFA/`auth_time`, revogação e logs podem ser centralizados | dependência do IdP; exige cadastro, TLS, chaves e runbook | **RECOMENDADA**, condicionada a contrato verificável |
| C. Identidade Nexus existente | potencialmente adequada | desconhecido | poderia reduzir duplicação, mas nenhuma interface foi encontrada | **PENDENTE**; não assumir que existe |
| D. Serviço independente de aprovação federada | pode usar IdP corporativo e isolar privilégios | forte separação e trilha dedicada | maior custo, nova superfície e dependência | **VIÁVEL** se integração direta não puder ser segregada |

OIDC define `iss` e `sub` como base da identidade do sujeito, mas `sub` identifica
o usuário no emissor/cliente e não comprova por si só que duas contas pertencem a
pessoas diferentes. O `person_id` deve vir de diretório corporativo autorizado,
ser imutável/não reciclado e ter semântica documentada. Referências:
[OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html) e
[OAuth 2.0 Security BCP, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html).

## 4. Arquitetura recomendada

1. Usar um IdP corporativo existente; nunca implementar provedor próprio.
2. Fluxo Authorization Code com PKCE, redirect URIs exatas, `state` e `nonce`;
   validar assinatura, `iss`, `aud`, `exp`, `iat`, `nonce` e algoritmo permitido.
3. Resolver papéis server-side por grupos/entitlements contratados; não confiar em
   campos de formulário ou papéis fornecidos pelo navegador.
4. Resolver `person_id` no contrato corporativo. Se ele não existir ou não tiver
   garantia de unicidade/não reciclagem, bloquear a dupla aprovação.
5. Exigir MFA/step-up recente para aprovar, revogar e executar. Preferir método
   resistente a phishing quando disponível; ver
   [NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b.html).
6. Armazenar no aceite somente IDs necessários, issuer, método/instante de
   autenticação, papel, tentativa, referência, revisão e timestamps; não guardar
   ID/access token completo.
7. Revalidar sessão, revogação, papel, pessoa e escopo no consumo transacional.
8. Se IdP/RBAC estiver indisponível ou inconclusivo, falhar fechado.

Tokens devem ficar server-side, criptografados quando persistidos, nunca em URL,
log ou template; cookies devem ser `Secure`, `HttpOnly`, `SameSite` adequado,
curtos e rotacionados no login/step-up. Não armazenar refresh token sem análise
específica, rotação e revogação.

## 5. Modelo RBAC

| Operação | Technical | Operational | Executor | Auditor | Permission admin |
|---|:---:|:---:|:---:|:---:|:---:|
| Visualizar solicitação | ✓ | ✓ | ✓ | ✓ | — |
| Aprovar tecnicamente | ✓ | — | — | — | — |
| Aprovar operacionalmente | — | ✓ | — | — | — |
| Revogar a própria aprovação pendente | ✓ | ✓ | — | — | — |
| Executar homologação | — | — | ✓ | — | — |
| Consultar evidências | — | — | — | ✓ | — |
| Administrar permissões | — | — | — | — | ✓ |

Regras adicionais:

- dois aprovadores autenticados, com `subject_id` e `person_id` distintos;
- executor deve ser terceira pessoa e não pode possuir um dos aceites;
- acumular papéis não elimina a segregação de pessoas;
- aprovação é vinculada a tentativa, referência e revisão exatas;
- concessão de papel é auditada fora do fluxo de homologação;
- revogação após consumo não retrocede `envio_iniciado`;
- UI nunca é controle de autorização; cada operação é revalidada server-side.

## 6. Testes e critérios de aceitação de uma sprint futura

O protótipo offline testa matriz de menor privilégio, mesma pessoa em contas
distintas, mesma conta, executor-aprovador, papel incorreto, MFA, expiração,
frescor, divergência de escopo, sanitização e configuração de cookie esperada.

Antes de integrar, exigir:

- contrato oficial do IdP/Nexus, issuer e metadata assinada/HTTPS;
- significado e ciclo de vida de `sub`, `person_id`, grupos e claims de MFA;
- client registration, redirect URIs e segredo/chave geridos fora do código;
- ambientes de teste, revogação, indisponibilidade e rotação de chaves;
- testes de CSRF, login CSRF, fixation, logout, expiração e múltiplos workers;
- threat model e aprovação de privacidade/retenção;
- auditoria independente do código exato de integração.

## 7. Parecer

- Arquitetura de identidade: **ADEQUADA COM PRÉ-CONDIÇÕES**.
- Modelo RBAC: **GO PARA PLANEJAMENTO**.
- Login SSW atual como fonte de dupla aprovação: **NO-GO**.
- Integração ao runtime e homologação real: **NO-GO**.

