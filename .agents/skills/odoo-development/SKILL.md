---
name: odoo-development
description: Expert guidance for Odoo ERP development including Python ORM, XML views, and module architecture
---

# Odoo Development

You are an expert in Python, Odoo, and enterprise business application development.

## Key Development Principles

### Code Quality & Architecture

- Write clear, technical responses with precise Odoo examples in Python, XML, and JSON
- Leverage Odoo's ORM, API decorators, and XML view inheritance for modularity
- Follow PEP 8 standards and Odoo best practices
- Use descriptive naming aligned with Odoo conventions

### Structural Organization

- Separate concerns across models, views, controllers, data, and security
- Create well-documented `__manifest__.py` files
- Organize modules with clear directory structures

## ORM & Python Implementation

- Define models inheriting from `models.Model`
- Apply API decorators appropriately:
  - `@api.model` for model-level methods
  - `@api.multi` for recordset methods
  - `@api.depends` for computed fields
  - `@api.onchange` for UI field changes
- Create XML-based UI views (forms, trees, kanban, calendar, graphs)
- Use XML inheritance via `<xpath>` and `<field>` for modifications
- Implement controllers with `@http.route` for HTTP endpoints

## Error Management & Validation

- Utilize built-in exceptions (`ValidationError`, `UserError`)
- Enforce constraints via `@api.constrains`
- Implement robust validation logic
- Use try-except blocks strategically
- Leverage Odoo's logging system (`_logger`)
- Write tests using Odoo's testing framework

## Security & Access Control

- Define ACLs and record rules in XML
- Manage user permissions through security groups
- Prioritize security at all architectural layers
- Implement proper access rights in ir.model.access.csv files

## Internationalization & Automation

- Mark translatable strings with `_()`
- Leverage automated actions and server actions
- Use cron jobs for scheduled tasks
- Use QWeb for dynamic HTML templating

## Performance Optimization

- Optimize ORM queries with domain filters and context
- Cache static or rarely-updated data
- Offload intensive tasks to scheduled actions
- Simplify XML structures through inheritance
- Use prefetch_fields and compute methods efficiently

## Guiding Conventions

1. Apply "Convention Over Configuration"
2. Enforce security throughout all layers
3. Maintain modular architecture
4. Document comprehensively
5. Extend via inheritance, never modify core code

## Feature Documentation Convention

When implementing or changing functionality in this addon, document the user-visible feature, important behavior, changed models/views/assets, and any operational notes in `FEATURES.md`.

## Module Structure Best Practices

```
module_name/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── model_name.py
├── views/
│   └── model_name_views.xml
├── security/
│   ├── ir.model.access.csv
│   └── security_rules.xml
├── data/
│   └── data.xml
├── controllers/
│   ├── __init__.py
│   └── main.py
├── static/
│   └── src/
├── wizards/
│   ├── __init__.py
│   └── wizard_name.py
└── reports/
    └── report_templates.xml
```

## Model Definition Example

```python
from odoo import models, fields, api
from odoo.exceptions import ValidationError

class CustomModel(models.Model):
    _name = 'custom.model'
    _description = 'Custom Model'

    name = fields.Char(string='Name', required=True)
    active = fields.Boolean(default=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
    ], default='draft')

    @api.depends('name')
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.name

    @api.constrains('name')
    def _check_name(self):
        for record in self:
            if len(record.name) < 3:
                raise ValidationError("Name must be at least 3 characters")
```

## View Definition Example

```xml
<record id="custom_model_form" model="ir.ui.view">
    <field name="name">custom.model.form</field>
    <field name="model">custom.model</field>
    <field name="arch" type="xml">
        <form>
            <header>
                <field name="state" widget="statusbar"/>
            </header>
            <sheet>
                <group>
                    <field name="name"/>
                    <field name="active"/>
                </group>
            </sheet>
        </form>
    </field>
</record>
```
