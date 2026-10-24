# Instructions

This document focuses on what to build.

# Phase 0: Initialisation
The first step is to set up the pre-checks and initialisations for the project at run time.
First, we check if the required database tables exist and create them if they don't.
We can start with bassic requirements and then increasingly add more capbilities as we implement more complex cases.
therefore, for now, we might have the following sql tables:

### User
- user_id (primary key) : Unique Identifier for the user
- username (unique): The username adopted by the user
- liabilities (list[Account]) : List of liability accounts the user has. Each account is a foreign key to the Liability table. Currently, only Credit cards are supported
- assets (list[Account]) : List of assets the user has. Each account here is a foreign key to an accounts table. Currently on Savings and Checking accounts are supported
- categories (list[Category]) : List of categories that the user has created
- tags (list[Tag]) : List of tags that the user has created
- meta (json) : A json field to store any additional metadata about the user. This can be used to store any additional information about the user that doesn't fit into the other fields.
- simplefin_hash (string) : The hash of the user's SimpleFin account. This is used to verify that the user has access to their SimpleFin account and to prevent unauthorized access. (is this a good place to add?)

### Account (Abstract)
- account_id (primary key) : Unique Identifier for the account
- user_id (foreign key: [User.user_id]) : The user that owns this account
- account_type (enum) : The type of account. This can be either 'liability' or 'asset'. This is used to determine which table the account belongs to. If the account
- account_number (string) : The last 4 digit of the account number.

- display_name (unique) : f'{account_type} - {account_number}' This serves as a unique display name for the account. It is unique because the combination of account_type and account_number is unique.
- meta (json): A json field to store any additional metadata about the account. This can be used to store any additional information about the account that doesn't fit into the other fields.

### Liability [Extends Account]
- issuer_name : The issuer of the credit card (e.g. Capital One, Amex, Chase, etc.)
- liability_type (enum) : The type of liability account. This can be either 'credit_card' or 'loan'. Currently, only credit cards are supported.

### Asset [Extends Account]
- bank_name : The bank that holds the asset account (e.g. Chase, Wells Fargo, Marcus etc.)
- asset_type (enum) : The type of asset account. This can be either 'checking', 'savings'

### Transaction
- transaction_id (primary key) : Unique Identifier for the transaction (use SimpleFin transaction_id)
- user_id (foreign key: [User.user_id]) : The user that owns this transaction
- account_id (foreign key) : The account that this transaction belongs to. This can be either a liability account or an asset account.
- amount (int) : The amount of the transaction. The transaction amount is always stored as integer, in cents. For example, if the transaction amount is $10.50, it will be stored as 1050.
- currency (string) : The currency of the transaction. This is a 3 letter ISO 4217 currency code (e.g. USD, INR, EUR, GBP, etc.)
- transaction_type (enum) : The type of transaction. This can be either 'debit' or 'credit'. A debit will have a -1 multiplier and a credit will have a +1 multiplier. This is used to determine the net effect of the transaction on the account balance. Credit cards are usually debit first, and then credit when the user makes a payment (unless it is a secured credit card)
- transaction_date (datetime) : The date and time of the transaction
- description (string) : A description of the transaction
- merchant_name (string) : The name of the merchant where the transaction took place
- transfer_id (optional foreign key: [Transfer.transfer_id]) : The transfer id of the transaction. This is used to link transactions that are part of the same transfer. For example, if a user transfers money from their checking account to their savings account, there will be two transactions with the same transfer_id. One transaction will be a debit from the checking account and the other will be a credit to the savings account.
- categories (json) : A json field to store categories and split percentages for the transaction. Categories are defined by the user in User.categories. The json field can be used to store a list of categories and their corresponding split percentages. For example, if a transaction is split between two categories, the json field can look like this: {"category1": 0.6, "category2": 0.4}. This means that 60% of the transaction amount is allocated to category1 and 40% is allocated to category2.
- tags (list[User.tags]) : List of tags associated with the transaction, as defined by the user in User.tags
- meta (json) : A json field to store any additional metadata about the transaction. This can be used to store any additional information about the transaction that doesn't fit into the other fields.

### Transfer
- transfer_id (primary key) : Unique Identifier for the transfer
- user_id (foreign key: [User.user_id]) : The user that owns this transfer
- debit_transaction_id (foreign key: [Transaction.transaction_id]) : The debit side of the associated transaction.
- credit_transaction_id (foreign key: [Transaction.transaction_id]) : The credit side of the associated transaction.
- transfer_amount (int) : The amount of the transfer. The transfer amount is always stored as integer, in cents. For example, if the transfer amount is $10.50, it will be stored as 1050. (Validate that the debit amount == credit amount == transfer amount)
- transfer_date (datetime) : The date and time of the transfer
- description (string) : A description of the transfer
- meta (json) : A json field to store any additional metadata about the transfer. This can be used to store any additional information about the transfer that doesn't fit into the other fields.

----

## Phase 1: Setup
Once checked if the tables exist, we need to check if user data exists. 
Right now, we are only handling one user, so we can just read and load the top 1 user. Multi user management can come later
Next we check for SimpleFin credentials. If they don't exist, we need to prompt the user to enter them. Once we have the credentials, we can use them to fetch the user's data from SimpleFin and store it in our database.

## Phase 2: Fetching Data
Once we have the user's SimpleFin credentials, we can use them to fetch the user's data from SimpleFin.
SimpleFin's `/accounts` endpoint returns accounts directly — each account in the response
already carries its type-ish info (name, balance) and its own nested list of transactions.
So we don't infer accounts from transaction data; we parse the `/accounts` response into
`Account` rows (deciding `asset` vs `liability`, and `asset_type`/`liability_type`, by
guessing from the account name since SimpleFin doesn't label it directly) and parse each
account's nested transactions into `Transaction` rows in the same pass.
