"""
Copyright 2024-2025 Confluent, Inc.
"""
import os, argparse
from typing import TypedDict, Tuple

from pathlib import Path
from langgraph.graph import END, StateGraph
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama.llms import OllamaLLM

import re


parser = argparse.ArgumentParser(
    prog=os.path.basename(__file__),
    description='Test Flink sql generation from a dbt sql file'
)
parser.add_argument('-n', '--file_path', required=True, help="name of the file path with the sql file to process")

"""
Create Flink SQL with a set of agents working within the following workflow

1. translate (dbt sql string to the matching Fink sql )
2. remove some sql line we do not need in the flink sql as the dml is insert into table ...
3. be sure the Flink SQL is syntactically correct
4. Generate the matching DDL for the dml
"""




model_name=os.getenv("LLM_MODEL","qwen2.5-coder:32b")
llm_base_url=os.getenv("LLM_BASE_URL","http://localhost:11434")
model = OllamaLLM(model=model_name, base_url=llm_base_url)


translator_prompt_template = """
you are qwen-coder, a code assistant, expert in Apache Flink SQL and  DBT (Data Build Tool). 
Translate the following DML SQL batch script into Apache Flink SQL for real-time processing.
Replace dbt utility functions with equivalent Flink SQL functions.
Maintain the original logic and functionality.

* Keep all the select statements defined with the WITH keyword.
* Do not add suggestions or explanations in the response, just return the structured Flink sql output.
* Do not use VARCHAR prefer STRING. 
* Transform the dbt function `surrogate_key` to a MD5(CONCAT_WS()) equivalent.  
* Start the contact argument with the char , as a first field to concat with, as an example: MD5(CONCAT_WS(','))
* end each line with , for the select part of the query. Do not start a row content with ','
* change left anti join to left join with where condition on the right side key. For example: 
the SQL: "left anti join left_table on right_table.role_id = left_table.role_id and  right_table.__db = left_table.tenant_id"
should be changed to: "left join left_table on right_table.role_id = left_table.role_id and  right_table.__db = left_table.tenant_id where right_table.role_id is null or right_table.__db is null"
* for any '_pk_fk substring change to '_sid' 
* remove line with the string: ```sql
* remove line with  ``` string

Start the generated code with:

INSERT INTO {table_name}

Question: {sql_input}
"""

flink_sql_syntaxic_template="""
you are qwen-coder, a code assistant, expert in Apache Flink SQL syntax. Your goal is to generate a syntactically correct Apache Flink SQL
statement from the following Flink SQL statement: {flink_sql}.

Use back quote character like ` around column name when column name matches any SQL keywords. As an example a column named value should be `value` 
while a column named tenant_id should be tenant_id. 

* remove  line with the string ```sql
* remove line with only ```  string

Do not generate explanations for the fixes you did.
"""

flink_ddl_generation_template="""
you are qwen-coder, a code assistant, expert in Apache Flink SQL. Your goal is to build a CREATE TABLE Apache Flink SQL
statement from the current flink sql insert into statement like:

{flink_sql}

The resulting ddl for the table {table_name} should include STRING for any _id column.

Do not use VARCHAR prefer STRING. 
a CONCAT needs to be translated to CONCAT_WS.

Use CREATE TABLE IF NOT EXISTS instead of CREATE TABLE

Use back quote character like ` around column name which is one of the SQL keyword. As an example a column name should be `name`. 

Remove column named: dl_landed_at, __ts_ms, __source_ms within create table or select statement.

Finish the statement with the following declaration:
   PRIMARY KEY(sid) NOT ENFORCED -- VERIFY KEY
) WITH ( 
   'changelog.mode' = 'retract',
   'kafka.cleanup-policy'= 'compact',
   'key.avro-registry.schema-context' = '.flink-dev',
   'value.avro-registry.schema-context' = '.flink-dev',
   'key.format' = 'avro-registry',
   'value.format' = 'avro-registry',
   'kafka.retention.time' = '0',
   'kafka.producer.compression.type' = 'snappy',
   'scan.bounded.mode' = 'unbounded',
   'scan.startup.mode' = 'earliest-offset',
   'value.fields-include' = 'all'
)

* remove  the string ```sql line
* remove line with only ```  string

Do not generate explanations for the generated text you did.
"""

def extract_sql_blocks(text) -> str:
    """
    Extract SQL code blocks from a given text.
    
    Args:
        text (str): The input text containing SQL code blocks
    
    Returns:
        list: A list of SQL code blocks found in the text
    """
    if text.find("```sql") > 0:
        sql_blocks = re.findall(r'```sql\n(.*?)```', text, re.DOTALL) 
        sql_block = ""
        for line in sql_blocks: # back in one string
            sql_block += line
        return sql_block
    else:
        return text
    
def remove_noise_in_sql(sql: str) -> str:
    """
    remove final AS ( 
    and SELECT * FROM final
    """
    newone=sql.replace(" final AS (","").replace("CURRENT_DATE()", "event_ts")
    return newone.replace("SELECT * FROM final","")


class AgentState(TypedDict):
    sql_input: str
    table_name: str
    flink_sql: str
    derived_ddl: str

def define_flink_sql_agent():
    
    def run_translator_agent(state):
        """
        change the sql_input string to the matching flink sql statement, and keep in state.flink_sql
        """
        print(f"\n--- Start the SQL `translator` AI agent ---")
        prompt = ChatPromptTemplate.from_template(translator_prompt_template) 
        chain = prompt | model 
        llm_out = chain.invoke(state)
        flink_sql = remove_noise_in_sql(llm_out)
        print(f"\n--- Done with SQL `translator` AI agent: \n{flink_sql}")
        return {"flink_sql": flink_sql}

    def syntax_cleaner(state):
        """
        Verify the Flink SQL syntax is correct
        """
        print(f"\n--- Start SQL `syntax cleaner` agent ---")
        prompt = ChatPromptTemplate.from_template(flink_sql_syntaxic_template) 
        chain = prompt | model 
        agent_outcome = chain.invoke(state)
        print(f"--- Done SQL `syntax cleaner` agent:\n{agent_outcome} ---")
        return {"flink_sql": agent_outcome}

    def ddl_generation(state):
        print(f"\n--- Start DDL Generation AI Agent ---")
        prompt = ChatPromptTemplate.from_template(flink_ddl_generation_template) 
        chain = prompt | model 
        llm_out = chain.invoke(state)
        derived_ddl = remove_noise_in_sql(llm_out)
        print(f" --- Done DDL generator Agent:\n{derived_ddl}")
        return {"derived_ddl": derived_ddl}

    def clean_sqls(state):
        print(f"\n--- Start clean_sql AI Agent ---")
        sql = state["flink_sql"]
        better_sql=extract_sql_blocks(sql)
        print(f" --- Done Clean SQL Agent: \n{better_sql}")
        return {"flink_sql": better_sql}
    
    

    workflow = StateGraph(AgentState)
    workflow.add_node("translator", run_translator_agent)  # will update flink_sql
    workflow.add_node("cleaner", clean_sqls)  # will update flink_sql
    workflow.add_node("syntaxic_cleaner", syntax_cleaner)  # will update flink_sql
    workflow.add_node("ddl_generation", ddl_generation)

    workflow.set_entry_point("translator")
    workflow.add_edge("translator", "syntaxic_cleaner")
    workflow.add_edge("syntaxic_cleaner","cleaner")
    workflow.add_edge("cleaner", "ddl_generation")
    workflow.add_edge("ddl_generation", END)
    app = workflow.compile()
    return app


def translate_to_flink_sqls(table_name: str, sql_content: str) -> Tuple[str,str]:
    app = define_flink_sql_agent()
    inputs = {"sql_input": sql_content, "table_name" : table_name}
    result=app.invoke(inputs)
    return result['flink_sql'], result['derived_ddl']
 


if __name__ == "__main__":
    args = parser.parse_args()
    source_path=args.file_path
    the_path= Path(source_path)
    table_name = the_path.stem
    with open(source_path) as f:
        sql_content= f.read()
        print(f"--- Input file {source_path} read ")
        a,b=translate_to_flink_sqls(table_name,sql_content)
        #print(a)
        #print(" ----- ")
        print(b)
    print("-"*40)
    print("done !")
