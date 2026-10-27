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