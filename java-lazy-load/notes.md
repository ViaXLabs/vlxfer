Short answer: **no, calling `a.getB().getId()` generally won't trigger a load of B**, as long as you're using JPA/Hibernate and the ID is mapped as B's identifier (`@Id`).

**Why:** When A is loaded and its B association is lazy, Hibernate doesn't leave `b` as null. It puts in a **proxy**, a stand-in subclass of B that already knows B's ID, because that's the foreign key value sitting in A's row. Calling `getId()` on the proxy just returns that stored value. No query fires.

**When it does hit the database:** the moment you call something that isn't the ID, like `a.getB().getName()`. The proxy then runs a `SELECT` on table B, fills itself in, and returns the name.

**Gotchas:**

1. **Access type matters.** If you put `@Id` on the *field* (field access), Hibernate can answer `getId()` from the proxy without initializing. With *property access* (annotations on getters), older Hibernate versions would initialize the proxy even for `getId()`. Modern Hibernate (5.x and later) handles both fine, but it's worth knowing if you're on an old version.
2. **It only applies to the identifier.** Other fields, even if they look like simple columns, will force a load. If you only need the ID, use it directly or add a separate mapped column like `@Column(name = "b_id", insertable = false, updatable = false) private UUID bId;` on A.
3. **`@ManyToOne` default is EAGER.** In JPA, `@ManyToOne` and `@OneToOne` are eager by default, so you have to set `fetch = FetchType.LAZY` explicitly for this behavior. `@OneToMany` and `@ManyToMany` are lazy by default.
4. **Lazy loading needs an open session.** Touching a non-ID field on an uninitialized proxy after the session/transaction closes throws `LazyInitializationException`. Calling `getId()` is safe even then.
5. **UUIDs work the same as any other ID type.** The type doesn't change the behavior. The proxy holds the UUID from the foreign key column.

So for your question, `a.getB().getId()` returns the UUID straight from the proxy with no extra query, while `a.getB().getName()` triggers the load of B.


Here are the official docs that cover this.

**Hibernate Javadocs (the most direct source)**
- `LazyInitializer` (Hibernate 7.2): https://docs.hibernate.org/orm/7.2/javadocs/org/hibernate/proxy/LazyInitializer.html
- `LazyInitializer` (stable): https://docs.jboss.org/hibernate/stable/orm/javadocs/org/hibernate/proxy/LazyInitializer.html
- `AbstractLazyInitializer`: https://docs.jboss.org/hibernate/stable/orm/javadocs/org/hibernate/proxy/AbstractLazyInitializer.html

This is the interface behind every Hibernate proxy. It has a method that retrieves the identifier value of the entity the proxy represents without initializing the proxy, which is why `getId()` on a lazy reference doesn't hit the database.

**One caveat the docs add to my earlier answer:** the same Javadoc notes that when JPA proxy compliance is enabled, the proxy is initialized. If your project turns on `hibernate.jpa.compliance.proxy`, then `getId()` will trigger a load. It's off by default, so you'd only hit this if someone enabled it.

**Related Javadoc methods**
- `Hibernate.isInitialized(Object)` checks whether a proxy has been loaded yet.
- `Hibernate.initialize(Object)` forces a proxy to load. Use it before the session closes if you'll need its fields later.

**Also worth reading (I didn't pull links for these)**
- The Hibernate ORM User Guide, in the "Fetching" and "Associations" chapters, covers lazy loading, proxies, and `@ManyToOne` fetch defaults.
- The Jakarta Persistence spec and Javadoc for `jakarta.persistence.FetchType` and `@ManyToOne`. The spec treats `LAZY` as a hint that the provider may ignore, while Hibernate honors it through proxies.

If you meant something different by "doc form," like a Word or Google doc summarizing this, let me know and I can put it together.~~~~



-----


**Oracle/Java docs**

- FetchType (Java EE 6): https://docs.oracle.com/javaee/6/api/javax/persistence/FetchType.html
- FetchType (Java EE 5): https://docs.oracle.com/javaee/5/api/javax/persistence/FetchType.html

These docs say that LAZY is only a hint to the persistence provider that data should be fetched when first accessed, and the implementation is allowed to fetch it eagerly anyway. They also say that EAGER is a hard requirement.

**The Oracle/Java docs don't guarantee that `getId()` skips the load.** That behavior is Hibernate's, from the `LazyInitializer` Javadoc I linked earlier. The Java spec only covers the lazy hint. So the "no query on `getId()`" part depends on you using Hibernate as the JPA provider.

**Example to run on your data**

Swap in your own entities. Turn on `hibernate.show_sql=true` so you can see exactly when SQL fires.

```java
@Entity
class Customer {                       // "B"
    @Id private UUID id;
    private String name;
    public UUID getId() { return id; }
    public String getName() { return name; }
}

@Entity
class Orders {                         // "A"
    @Id private UUID id;
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;
    public Customer getCustomer() { return customer; }
}

try (Session s = sessionFactory.openSession()) {
    Orders o = s.find(Orders.class, orderId);          // SQL #1: orders only
    Customer c = o.getCustomer();

    System.out.println("proxy: " + (c instanceof HibernateProxy));
    System.out.println("loaded before: " + Hibernate.isInitialized(c));

    System.out.println("id: " + c.getId());            // should fire no SQL
    System.out.println("loaded after getId: " + Hibernate.isInitialized(c));

    System.out.println("name: " + c.getName());        // SQL #2: customer
    System.out.println("loaded after getName: " + Hibernate.isInitialized(c));
}
```

**What you should see if it works as described**

```
Hibernate: select ... from orders where id=?
proxy: true
loaded before: false
id: <the uuid>
loaded after getId: false
Hibernate: select ... from customer where id=?
name: <the name>
loaded after getName: true
```

**I can't give you real results.** I have no access to your database, and my sandbox has no network to pull Hibernate. The output above is what the docs predict, not something I ran. Paste your actual output here and I'll help read it. If you see a `customer` SELECT right after the orders query, check that `fetch = LAZY` is set and that JPA proxy compliance isn't turned on.~~~~

----

Yes, adding a second read-only mapping for the foreign key is a common and good option. Here are the main choices.

**1. Read-only FK column on A (what you described)**

```java
@Entity
class Orders {
    @Id private UUID id;

    @Column(name = "customer_id", insertable = false, updatable = false)
    private UUID customerId;                 // plain column, always loaded with A

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;               // this one is the writable mapping
}
```

`getCustomerId()` is just a field on A, so there's no proxy involved and no extra query. One of the two mappings to the same column has to be read-only, or Hibernate complains about a repeated column. Making the plain ID the read-only one is the usual choice.

**2. Projection queries (load only the columns you ask for)**

```java
// DTO projection
List<OrderSummary> rows = em.createQuery(
    "select new com.acme.OrderSummary(o.id, o.customer.id) from Orders o",
    OrderSummary.class).getResultList();
```

This selects only those columns and returns no entities at all. `o.customer.id` doesn't join to the customer table, because Hibernate reads the FK column directly. This is the best option for list screens and reports. Spring Data's interface projections do the same thing.

**3. Fetch just one column of B later**

```java
String name = em.createQuery(
    "select c.name from Customer c where c.id = :id", String.class)
    .setParameter("id", customerId).getSingleResult();
```

**To answer your "will it load the whole object" question:**

- **Proxy:** touching any non-ID getter loads B's whole row, meaning all its basic columns. B's own lazy associations stay as proxies, but any eager ones load too.
- **`em.find(Customer.class, customerId)`:** also loads the whole row.
- **Projection query:** loads only the columns you select.

So if you want one field out of B without the rest, use a projection query rather than the proxy.

**4. When you do want B, but without N+1 queries**

If you're looping over many A's and will need each B, a lazy proxy fires one query per row. Load them together instead with `join fetch`:

```java
"select o from Orders o join fetch o.customer"
```

or with an `@EntityGraph`. That's one query that loads both.

**5. If B itself is heavy (big blobs/text)**

Move the big columns to a separate entity, or mark them `@Basic(fetch = LAZY)`. That only takes effect with Hibernate bytecode enhancement turned on, because the Java docs treat LAZY as a hint.

**My pick:**
- For "I just need B's ID," use the read-only `customerId` column. It's simple, has no proxy gotchas, and is safe after the session closes.
- For lists and reports, use DTO projections.
- Use the lazy proxy when you usually don't need B but sometimes do.

If you tell me whether you're using plain Hibernate or Spring Data JPA, I can tailor the example.

---

Here's the plain Hibernate version, with a few Hibernate-specific tools worth knowing about.

**1. Entities: read-only FK column plus lazy association**

```java
@Entity
public class Orders {
    @Id
    private UUID id;

    @Column(name = "customer_id", insertable = false, updatable = false)
    private UUID customerId;                    // just a column, always loaded with Orders

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;                  // the writable mapping

    public UUID getCustomerId() { return customerId; }
    public Customer getCustomer() { return customer; }
}
```

**2. Read just the IDs with a projection (Hibernate 6 style)**

```java
try (Session s = sessionFactory.openSession()) {
    List<Object[]> rows = s.createQuery(
        "select o.id, o.customerId from Orders o", Object[].class)
        .getResultList();
}
```

You can also use `select new com.acme.OrderSummary(o.id, o.customerId) from Orders o` for a DTO, or a Java record. Either way, no entities are loaded and only those two columns are selected. On Hibernate 5, use `createQuery(hql, Object[].class)` the same way.

**3. Pull one field of B without loading the whole row**

```java
String name = s.createQuery(
    "select c.name from Customer c where c.id = :id", String.class)
    .setParameter("id", customerId)
    .getSingleResult();
```

**4. Hibernate-specific tools**

- **`session.getReference(Customer.class, id)`** returns a proxy without a SELECT. Use it when writing, to set a foreign key without loading the other row:

```java
Orders o = new Orders();
o.setCustomer(s.getReference(Customer.class, customerId));  // no query on customer
s.persist(o);
```

- **`Hibernate.isInitialized(x)`** checks whether a proxy has been loaded, and **`Hibernate.initialize(x)`** forces the load while the session is still open.
- **`@BatchSize(size = 50)`** on `Customer` (or on the association) helps when you loop over many Orders and touch each customer. Instead of one SELECT per customer, Hibernate loads proxies in batches with an `IN (...)` query.
- **`join fetch`** loads both rows in one query when you know you'll need B:

```java
s.createQuery("select o from Orders o join fetch o.customer", Orders.class)
 .getResultList();
```

- **Bytecode enhancement** (the Hibernate Maven/Gradle plugin) is what makes `@Basic(fetch = LAZY)` actually work for big columns like blobs. Without it, Hibernate loads those columns anyway.

**Quick decision guide**

| You need | Use |
|---|---|
| Just B's ID | `customerId` column |
| Lists/reports | HQL projection or DTO |
| One field from B | Single-column HQL query |
| B's full data for many A's | `join fetch` or `@BatchSize` |
| Set a FK on insert | `getReference` |
| Sometimes B, usually not | Lazy proxy |

One thing to watch: with the proxy approach, any non-ID getter loads the entire B row. If that's the case you're trying to avoid, use the single-column query from section 3.

If you tell me your Hibernate version (5.x or 6.x), I can adjust the syntax to match it.
---

Here's the whole thing in one piece you can pass along.

---

## Lazy loading and getting a foreign key ID without loading the other object (Hibernate 7, Spring)

**The question:** Table A has a foreign key to table B, mapped lazily. If you call `a.getB().getId()`, does it load all of B?

**Short answer:** With Hibernate, no. `a.getB()` returns a proxy that already knows B's ID from A's foreign key column, so `getId()` returns it with no query. Calling any other getter, like `getName()`, loads B's whole row.

### What the docs say

- The Oracle/Java docs for `FetchType` say that LAZY is only a hint to the persistence provider, and the implementation is allowed to fetch eagerly anyway, while EAGER is a hard requirement. https://docs.oracle.com/javaee/6/api/javax/persistence/FetchType.html
- The `getId()` shortcut isn't in the Java spec. It's Hibernate's behavior, documented in the `LazyInitializer` Javadoc, which has a method to get a proxy's identifier without initializing the proxy. https://docs.hibernate.org/orm/7.2/javadocs/org/hibernate/proxy/LazyInitializer.html
- That Javadoc also notes an exception: when JPA proxy compliance is enabled, the proxy is initialized. It's off by default, but if someone turned it on, `getId()` will load B.

### Setup notes

- Hibernate 7 uses Jakarta Persistence, so imports are `jakarta.persistence.*`, not `javax`. That's normal in Spring and needs nothing extra.
- `@ManyToOne` is EAGER by default, so you must set `fetch = FetchType.LAZY` explicitly.
- Touching a non-ID field on an uninitialized proxy after the session closes throws `LazyInitializationException`. In Spring, lazy loading only works inside `@Transactional`.

### Entities (the recommended mapping)

```java
import jakarta.persistence.*;
import java.util.UUID;

@Entity
public class Orders {                        // "A"
    @Id
    private UUID id;

    @Column(name = "customer_id", insertable = false, updatable = false)
    private UUID customerId;                 // plain column, loaded with Orders, no proxy

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "customer_id")
    private Customer customer;               // lazy proxy, the writable mapping

    public UUID getCustomerId() { return customerId; }
    public Customer getCustomer() { return customer; }
}

@Entity
public class Customer {                      // "B"
    @Id
    private UUID id;
    private String name;
    public UUID getId() { return id; }
    public String getName() { return name; }
}
```

Mapping the same column twice is allowed as long as one mapping is read-only. If you only need B's ID, use `getCustomerId()`. It's a plain field, has no proxy gotchas, and works after the session closes.

### Test it on your own data

```java
@Service
public class OrderService {
    @PersistenceContext
    private EntityManager em;

    @Transactional(readOnly = true)
    public void demo(UUID orderId) {
        Orders o = em.find(Orders.class, orderId);              // SQL #1: orders only
        Customer c = o.getCustomer();

        System.out.println("loaded before: " + Hibernate.isInitialized(c));   // false
        System.out.println("id: " + c.getId());                                // no SQL
        System.out.println("loaded after getId: " + Hibernate.isInitialized(c)); // false
        System.out.println("name: " + c.getName());                            // SQL #2
        System.out.println("loaded after getName: " + Hibernate.isInitialized(c)); // true
    }
}
```

Turn on `hibernate.show_sql=true` (or `spring.jpa.show-sql=true` in Boot) and watch when SQL fires. If you see a customer SELECT right after the orders query, check that `fetch = LAZY` is set and that proxy compliance isn't on. I haven't run this against a real database, so these are the expected results, not verified output.

### Your options for loading less

| You need | Use |
|---|---|
| Just B's ID | the read-only `customerId` column |
| Lists and reports | HQL projection or DTO |
| One field of B | single-column query |
| B's full data for many A's | `join fetch`, `@BatchSize`, or an entity graph |
| Set a foreign key on insert | `em.getReference` |
| Usually not B, sometimes B | lazy proxy |

```java
// IDs only, no entities loaded
em.createQuery("select o.id, o.customerId from Orders o", Object[].class).getResultList();

// One field of B, without loading the whole row
em.createQuery("select c.name from Customer c where c.id = :id", String.class)
  .setParameter("id", customerId).getSingleResult();

// Set a FK without selecting the customer
o.setCustomer(em.getReference(Customer.class, customerId));

// Load both in one query (avoids one-query-per-row)
em.createQuery("select o from Orders o join fetch o.customer", Orders.class).getResultList();
```

Other tools:
- `@org.hibernate.annotations.BatchSize(size = 50)` on `Customer` batches lazy loads into `IN (...)` queries.
- An entity graph via the hint `jakarta.persistence.fetchgraph` fetches chosen associations.
- `Hibernate.initialize(x)` forces a load while the session is open.
- `@Basic(fetch = LAZY)` on big columns (blobs, long text) only works with Hibernate bytecode enhancement.

### The question your friend will probably ask

*"Does touching the proxy load the whole object?"* Yes. Any non-ID getter loads B's entire row. If you want a single field without the rest, use the single-column query above.

### Spring pitfalls

- Return DTOs, or use `customerId`, instead of passing proxies out of the service layer.
- Spring Boot enables Open Session in View by default (`spring.jpa.open-in-view=true`). It hides `LazyInitializationException` but keeps the session open for the whole web request. Many teams turn it off.
- If you use Spring Data JPA repositories, interface projections and `@EntityGraph` do the same jobs with less code.

---

