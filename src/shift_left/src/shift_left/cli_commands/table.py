"""
Copyright 2024-2025 Confluent, Inc.
"""
import typer
import os
from importlib import import_module
from rich import print
from typing_extensions import Annotated
from shift_left.core.table_mgr import (
    search_source_dependencies_for_dbt_table, 
    get_short_table_name, 
    update_makefile_in_folder, 
    validate_table_cross_products,
    update_sql_content_for_file,
    update_all_makefiles_in_folder,
)
from shift_left.core.process_src_tables import migrate_one_file
from shift_left.core.utils.file_search import list_src_sql_files
from shift_left.core.utils.app_config import shift_left_dir
import shift_left.core.table_mgr as table_mgr
import shift_left.core.test_mgr as test_mgr
from shift_left.core.test_mgr import TestSuiteResult

"""
Manage the table entities.
- build an inventory of all the tables in the project with the basic metadata per table
- deploy a table taking care of the children Flink statements to stop and start
- 
"""
app = typer.Typer()

@app.command()
def init(table_name: Annotated[str, typer.Argument(help="Table name to build")],
         table_path: Annotated[str, typer.Argument(help="Folder Path in which the table folder structure will be created.")],
         product_name: str = typer.Option(default=None, help="Product name to use for the table. If not provided, it will use the table_path last folder as product name")):
    """
    Build a new table structure under the specified path. For example to add a source table structure use for example the command:
    `shift_left table init src_table_1 $PIPELINES/sources/p1`
    """
    print("#" * 30 + f" Build Table in {table_path}")
    table_folder, table_name= table_mgr.build_folder_structure_for_table(table_name, table_path, product_name)
    print(f"Created folder {table_folder} for the table {table_name}")

@app.command()
def build_inventory(pipeline_path: Annotated[str, typer.Argument(envvar=["PIPELINES"], help= "Pipeline folder where all the tables are defined, if not provided will use the $PIPELINES environment variable.")]):
    """
    Build the table inventory from the PIPELINES path.
    """
    print("#" * 30 + f" Build Inventory in {pipeline_path}")
    inventory= table_mgr.get_or_create_inventory(pipeline_path)
    print(inventory)
    print(f"--> Table inventory created into {pipeline_path} with {len(inventory)} entries")

@app.command()
def search_source_dependencies(table_sql_file_name: Annotated[str, typer.Argument(help="Full path to the file name of the dbt sql file")],
                                src_project_folder: Annotated[str, typer.Argument(envvar=["SRC_FOLDER"], help="Folder name for all the dbt sources (e.g. models)")]):
    """
    Search the parent for a given table from the source project (dbt, sql or ksql folders).
    Example: shift_left table search-source-dependencies $SRC_FOLDER/ 
    """
    if not table_sql_file_name.endswith(".sql"):
        exit(1)
    print(f"The dependencies for {table_sql_file_name} from the {src_project_folder} project are:")
    dependencies = search_source_dependencies_for_dbt_table(table_sql_file_name, src_project_folder)
    table_name = get_short_table_name(table_sql_file_name)
    print(f"Table {table_name} in the SQL {table_sql_file_name} depends on:")
    for table in dependencies:
        print(f"  - {table['table']} (in {table['src_dbt']})")
    print("#" * 80)
        

@app.command()
def migrate(
        table_name: Annotated[str, typer.Argument(help= "the name of the table once migrated.")],
        sql_src_file_name: Annotated[str, typer.Argument(help= "the source file name for the sql script to migrate.")],
        target_path: Annotated[str, typer.Argument(envvar=["STAGING"], help ="the target path where to store the migrated content (default is $STAGING)")],
        recursive: bool = typer.Option(False, "--recursive", help="Indicates whether to process recursively up to the sources. (default is False)")):
    """
    Migrate a source SQL Table defined in a sql file with AI Agent to a Staging area to complete the work. 
    The command uses the SRC_FOLDER to access to src_path folder.
    """
    print("#" * 30 + f" Migrate source SQL Table defined in {sql_src_file_name}")
    if not sql_src_file_name.endswith(".sql"):
        print("[red]Error: the sql_src_file_name parameter needs to be a dml sql file[/red]")
        exit(1)
    if not os.getenv("SRC_FOLDER") and not os.getenv("STAGING"):
        print("[red]Error: SRC_FOLDER and STAGING environment variables need to be defined.[/red]")
        exit(1)
    print(f"Migrate source SQL Table defined in {sql_src_file_name} to {target_path} {'with ancestors' if recursive else ''}")
    migrate_one_file(table_name, sql_src_file_name, target_path, os.getenv("SRC_FOLDER"), recursive)
    print(f"Migrated content to folder {target_path} for the table {sql_src_file_name}")

@app.command()
def update_makefile(
        table_name: Annotated[str, typer.Argument(help= "Name of the table to process and update the Makefile from.")],
        pipeline_folder_name: Annotated[str, typer.Argument(envvar=["PIPELINES"], help= "Pipeline folder where all the tables are defined, if not provided will use the $PIPELINES environment variable.")]):
    """ Update existing Makefile for a given table or build a new one """

    update_makefile_in_folder(pipeline_folder_name, table_name)
    print(f"Makefile updated for table {table_name}")

@app.command()
def update_all_makefiles(
        folder_name: Annotated[str, typer.Argument(envvar=["PIPELINES"], help= "Folder from where all the Makefile will be updated. If not provided, it will use the $PIPELINES environment variable.")]):
    """ Update all the Makefiles for all the tables in the given folder. Example: shift_left table update-all-makefiles $PIPELINES/dimensions/product_1
    """
    count = update_all_makefiles_in_folder(folder_name)
    print(f"Updated {count} Makefiles in {folder_name}")



@app.command()
def validate_table_names(pipeline_folder_name: Annotated[str, typer.Argument(envvar=["PIPELINES"],help= "Pipeline folder where all the tables are defined, if not provided will use the $PIPELINES environment variable.")]):
    """
    Go over the pipeline folder to assess if table name,  naming convention, and other development best practices are respected.
    """
    print("#" * 30 + f"\nValidate_table_names in {pipeline_folder_name}")
    validate_table_cross_products(pipeline_folder_name)

@app.command()
def update_tables(folder_to_work_from: Annotated[str, typer.Argument(help="Folder from where to do the table update. It could be the all pipelines or subfolders.")],
                  ddl: bool = typer.Option(False, "--ddl", help="Focus on DDL processing. Default is only DML"),
                  both_ddl_dml: bool = typer.Option(False, "--both-ddl-dml", help="Run both DDL and DML sql files"),
                  string_to_change_from: str = typer.Option(None, "--string-to-change-from", help="String to change in the SQL content"),
                  string_to_change_to: str = typer.Option(None, "--string-to-change-to", help="String to change in the SQL content"),
                  class_to_use = Annotated[str, typer.Argument(help= "The class to use to do the Statement processing", default="shift_left.core.utils.table_worker.ChangeLocalTimeZone")]):
    """
    Update the tables with SQL code changes defined in external python callback. It will read dml or ddl and apply the updates.
    """
    print("#" * 30 + f"\nUpdate_tables from {folder_to_work_from} using the processor: {class_to_use}")
    files = list_src_sql_files(folder_to_work_from)
    files_to_process =[]
    if both_ddl_dml or ddl: # focus on DDLs update
        for file in files:
            if file.startswith("ddl"):
                files_to_process.append(files[file])
    if not ddl:
        for file in files:
            if file.startswith("dml"):
                files_to_process.append(files[file])
    if class_to_use:
        module_path, class_name = class_to_use.rsplit('.',1)
        mod = import_module(module_path)
        runner_class = getattr(mod, class_name)
        count=0
        processed=0
        for file in files_to_process:
            print(f"Assessing file {file}")    
            updated=update_sql_content_for_file(file, runner_class(), string_to_change_from, string_to_change_to)
            if updated:
                print(f"-> {file} processed ")
                processed+=1
            else:
                print(f"-> already up to date ")
            count+=1
    print(f"Done: processed: {processed} of {count} files!")


@app.command()
def init_unit_tests(table_name: Annotated[str, typer.Argument(help= "Name of the table to unit tests.")]):
    """
    Initialize the unit test folder and template files for a given table. It will parse the SQL statemnts to create the insert statements for the unit tests.
    It is using the table inventory to find the table folder for the given table name.
    """
    print("#" * 30 + f" Unit tests initialization for {table_name}")
    test_mgr.init_unit_test_for_table(table_name)
    print("#" * 30 + f" Unit tests initialization for {table_name} completed")

@app.command()
def run_test_suite(  table_name: Annotated[str, typer.Argument(help= "Name of the table to unit tests.")],
                test_case_name: str = typer.Option(default=None, help= "Name of the individual unit test to run. By default it will run all the tests"),
                compute_pool_id: str = typer.Option(default=None, envvar=["CPOOL_ID"], help="Flink compute pool ID. If not provided, it will use config.yaml one.")):
    """
    Run all the unit tests or a specified test case by sending data to `_ut` topics and validating the results
    """
    print("#" * 30 + f" Unit tests execution for {table_name}")
    if test_case_name:
        print(f"Running test case {test_case_name} for table {table_name} on compute pool {compute_pool_id}")
        test_result = test_mgr.execute_one_test(table_name, test_case_name, compute_pool_id)
        # review this
        test_suite_result = TestSuiteResult(foundation_statements=test_result.foundation_statements, 
                                            test_results={test_case_name: test_result})
        print(f"Valdidation test: {test_result.result}")
    else:
        test_suite_result  = test_mgr.execute_all_tests(table_name, compute_pool_id)
    file_name = f"{shift_left_dir}/{table_name}-test-suite-result.json"
    with open(file_name, "w") as f:
        f.write(test_suite_result.model_dump_json(indent=2))
    print(f"Test suite report saved into {file_name}")
    print("#" * 30 + f" Unit tests execution for {table_name} completed")



@app.command()
def delete_tests(table_name: Annotated[str, typer.Argument(help= "Name of the table to unit tests.")],
                 compute_pool_id: str = typer.Option(default=None, envvar=["CPOOL_ID"], help="Flink compute pool ID. If not provided, it will use config.yaml one.")):
    """
    Delete the Flink statements and kafka topics used for unit tests for a given table.
    """
    print("#" * 30 + f" Unit tests deletion for {table_name}")
    if os.path.exists(f"{shift_left_dir}/{table_name}-test-suite-result.json"):
        try:
            with open(f"{shift_left_dir}/{table_name}-test-suite-result.json", "r") as f:
                test_suite_result = TestSuiteResult.model_validate_json(f.read())
        except Exception as e:
            # this could happened if file was wrong.
            test_suite_result = None
    else:
        test_suite_result = None
    test_mgr.delete_test_artifacts(table_name, compute_pool_id, test_suite_result)
    print("#" * 30 + f" Unit tests deletion for {table_name} completed")

@app.command()
def explain(table_name: str=  typer.Option(None,help= "Name of the table to get Flink execution plan explanations from."),
            product_name: str = typer.Option(None, help="The directory to run the explain on each tables found within this directory. table or dir needs to be provided."),
            table_list_file_name: str = typer.Option(None, help="The file containing the list of tables to deploy."),
            compute_pool_id: str = typer.Option(default=None, envvar=["CPOOL_ID"], help="Flink compute pool ID. If not provided, it will use config.yaml one."),
            persist_report: bool = typer.Option(False, "--persist-report", help="Persist the report in the shift_left_dir folder.")):
    """
    Get the Flink execution plan explanations for a given table or a group of table using the product name.
    """
    
    if table_name:
        print("#" * 30 + f" Flink execution plan explanations for {table_name}")
        table_report=table_mgr.explain_table(table_name=table_name, 
                                             compute_pool_id=compute_pool_id, 
                                             persist_report=persist_report)
        print(f"Table: {table_report['table_name']}")
        print("-"*50)
        print(table_report['trace'])
        print("#" * 30 + f" Flink execution plan explanations for {table_name} completed")
    elif product_name:
        print("#" * 30 + f" Flink execution plan explanations for the product: {product_name}")
        tables_report=table_mgr.explain_tables_for_product(product_name=product_name, 
                                                           compute_pool_id=compute_pool_id, 
                                                           persist_report=persist_report)
        print(tables_report)
        print("#" * 30 + f" Flink execution plan explanations for the product {product_name} completed")
    elif table_list_file_name:
        print("#" * 30 + f" Flink execution plan explanations for the tables in {table_list_file_name}")
        tables_report=table_mgr.explain_tables_for_list_of_tables(table_list_file_name=table_list_file_name, 
                                                                  compute_pool_id=compute_pool_id, 
                                                                  persist_report=persist_report)
        print(tables_report)
        print("#" * 30 + f" Flink execution plan explanations for the tables in {table_list_file_name} completed")
    else:
        print("[red]Error: table or dir needs to be provided.[/red]")
        exit(1)
    